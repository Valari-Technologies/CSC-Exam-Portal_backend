"""Test and TestAssignment API views.

Permission model:
- CSC Admin: full access across all schools.
- School Admin / Teacher: CRUD within own school.
- Student: no access.
"""
from __future__ import annotations

import logging

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.questions.models import Question

from .models import Test, TestAssignment, TestQuestion
from .services import create_assignment_notifications
from .serializers import (
    AutoGenerateSerializer,
    TestAssignmentListSerializer,
    TestAssignmentWriteSerializer,
    TestDetailSerializer,
    TestListSerializer,
    TestQuestionWriteSerializer,
    TestWriteSerializer,
)

logger = logging.getLogger(__name__)

MANAGE_ROLES = ('csc_admin', 'school_admin', 'teacher')


class TestViewSet(viewsets.ModelViewSet):
    """Full CRUD for Test resources, school-scoped."""

    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'subject', 'school_class']
    search_fields = ['title', 'description']
    ordering_fields = ['title', 'created_at', 'duration_minutes', 'total_marks']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return TestListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return TestWriteSerializer
        return TestDetailSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Test.objects.none()

        qs = Test.objects.select_related(
            'subject', 'school_class', 'created_by', 'school',
        ).annotate(
            question_count=Count('test_questions'),
        )

        if user.role == 'csc_admin':
            return qs
        if user.role in ('school_admin', 'teacher') and user.school_id:
            return qs.filter(school_id=user.school_id)
        return Test.objects.none()

    def _require_manage_role(self) -> None:
        if self.request.user.role not in MANAGE_ROLES:
            raise PermissionDenied('Students cannot manage tests.')

    # -- CRUD overrides --

    def create(self, request, *args, **kwargs):
        self._require_manage_role()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        school = serializer.validated_data.pop('_school')
        test = Test.objects.create(
            **serializer.validated_data,
            school=school,
            created_by=request.user,
        )
        out = TestDetailSerializer(
            Test.objects.select_related(
                'subject', 'school_class', 'created_by', 'school',
            ).prefetch_related('test_questions__question', 'assignments').get(pk=test.pk),
        ).data
        return Response(out, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        self._require_manage_role()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._require_manage_role()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._require_manage_role()
        return super().destroy(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Prefetch for detail view
        instance = Test.objects.select_related(
            'subject', 'school_class', 'created_by', 'school',
        ).prefetch_related(
            'test_questions__question', 'assignments',
        ).get(pk=instance.pk)
        serializer = TestDetailSerializer(instance)
        return Response(serializer.data)

    # -- Custom actions --

    @action(detail=True, methods=['post'], url_path='add-questions')
    def add_questions(self, request, pk=None):
        """Bulk-add questions to a test."""
        self._require_manage_role()
        test = self.get_object()

        serializer = TestQuestionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question_ids: list[int] = serializer.validated_data['question_ids']

        # Validate questions exist, belong to same school, and match subject
        questions = Question.objects.filter(
            pk__in=question_ids,
            school_id=test.school_id,
            is_active=True,
        )
        valid_ids = set(questions.values_list('pk', flat=True))
        invalid_ids = set(question_ids) - valid_ids
        if invalid_ids:
            return Response(
                {'detail': f'Questions not found or not in same school: {sorted(invalid_ids)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Skip already-linked questions
        existing = set(
            TestQuestion.objects.filter(test=test, question_id__in=question_ids)
            .values_list('question_id', flat=True)
        )
        new_ids = [qid for qid in question_ids if qid not in existing]

        if not new_ids:
            return Response({'detail': 'All questions already added.'}, status=status.HTTP_200_OK)

        # Determine starting order_number
        max_order = (
            TestQuestion.objects.filter(test=test)
            .order_by('-order_number')
            .values_list('order_number', flat=True)
            .first()
        ) or 0

        test_questions = [
            TestQuestion(
                test=test,
                question_id=qid,
                order_number=max_order + idx,
            )
            for idx, qid in enumerate(new_ids, start=1)
        ]
        TestQuestion.objects.bulk_create(test_questions)

        # Return refreshed detail
        test = Test.objects.select_related(
            'subject', 'school_class', 'created_by', 'school',
        ).prefetch_related(
            'test_questions__question', 'assignments',
        ).annotate(
            question_count=Count('test_questions'),
        ).get(pk=test.pk)
        return Response(TestDetailSerializer(test).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='remove-questions')
    def remove_questions(self, request, pk=None):
        """Bulk-remove questions from a test."""
        self._require_manage_role()
        test = self.get_object()

        serializer = TestQuestionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question_ids: list[int] = serializer.validated_data['question_ids']

        deleted_count, _ = TestQuestion.objects.filter(
            test=test, question_id__in=question_ids,
        ).delete()

        # Return refreshed detail
        test = Test.objects.select_related(
            'subject', 'school_class', 'created_by', 'school',
        ).prefetch_related(
            'test_questions__question', 'assignments',
        ).annotate(
            question_count=Count('test_questions'),
        ).get(pk=test.pk)
        return Response(TestDetailSerializer(test).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='auto-generate')
    def auto_generate(self, request, pk=None):
        """Auto-generate questions for a test from the question bank."""
        self._require_manage_role()
        test = self.get_object()

        serializer = AutoGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        count: int = serializer.validated_data['count']
        difficulty: str | None = serializer.validated_data.get('difficulty') or None
        chapter_ids: list[int] | None = serializer.validated_data.get('chapter_ids') or None

        # Build queryset: same subject, same school, active questions
        question_qs = Question.objects.filter(
            subject_id=test.subject_id,
            school_id=test.school_id,
            is_active=True,
        )

        if difficulty:
            question_qs = question_qs.filter(difficulty=difficulty)

        if chapter_ids:
            question_qs = question_qs.filter(chapter_id__in=chapter_ids)

        # Exclude questions already linked to this test
        existing_question_ids = TestQuestion.objects.filter(
            test=test,
        ).values_list('question_id', flat=True)
        question_qs = question_qs.exclude(pk__in=existing_question_ids)

        # Random-select
        selected_questions = list(question_qs.order_by('?')[:count])

        if not selected_questions:
            return Response(
                {'detail': 'No matching questions found in the question bank.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Determine starting order_number
        max_order = (
            TestQuestion.objects.filter(test=test)
            .order_by('-order_number')
            .values_list('order_number', flat=True)
            .first()
        ) or 0

        test_questions = [
            TestQuestion(
                test=test,
                question=q,
                order_number=max_order + idx,
            )
            for idx, q in enumerate(selected_questions, start=1)
        ]
        TestQuestion.objects.bulk_create(test_questions)

        # Recalculate test.total_marks from all linked questions
        total = test.test_questions.aggregate(
            total=Sum(Coalesce('marks_override', 'question__marks')),
        )['total'] or 0
        test.total_marks = total
        test.save(update_fields=['total_marks', 'updated_at'])

        # Return refreshed detail
        test = Test.objects.select_related(
            'subject', 'school_class', 'created_by', 'school',
        ).prefetch_related(
            'test_questions__question', 'assignments',
        ).annotate(
            question_count=Count('test_questions'),
        ).get(pk=test.pk)
        return Response(TestDetailSerializer(test).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='publish')
    def publish(self, request, pk=None):
        """Publish a draft test (requires at least one question)."""
        self._require_manage_role()
        test = self.get_object()

        if test.status != Test.Status.DRAFT:
            return Response(
                {'detail': 'Only draft tests can be published.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not TestQuestion.objects.filter(test=test).exists():
            return Response(
                {'detail': 'Cannot publish a test with no questions.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        test.status = Test.Status.PUBLISHED
        test.save(update_fields=['status', 'updated_at'])

        log_action(
            request.user,
            AuditLog.Action.TEST_PUBLISHED,
            entity_type='Test',
            entity_id=test.pk,
            request=request,
            details={'title': test.title},
        )

        test = Test.objects.select_related(
            'subject', 'school_class', 'created_by', 'school',
        ).prefetch_related(
            'test_questions__question', 'assignments',
        ).annotate(
            question_count=Count('test_questions'),
        ).get(pk=test.pk)
        return Response(TestDetailSerializer(test).data)


class TestAssignmentViewSet(viewsets.ModelViewSet):
    """CRUD for TestAssignment resources, school-scoped."""

    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['test', 'is_active']
    ordering_fields = ['start_datetime', 'end_datetime', 'assigned_at']
    ordering = ['-start_datetime']

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return TestAssignmentWriteSerializer
        return TestAssignmentListSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return TestAssignment.objects.none()

        qs = TestAssignment.objects.select_related(
            'test', 'school_class', 'section', 'assigned_by',
        )

        if user.role == 'csc_admin':
            return qs
        if user.role in ('school_admin', 'teacher') and user.school_id:
            return qs.filter(test__school_id=user.school_id)
        if user.role == 'student':
            # Students see only assignments targeting them (mirrors the
            # eligibility rules in exams.serializers._validate_student_assigned)
            # for published tests in their own school.
            from apps.students.models import StudentProfile

            profile = (
                StudentProfile.objects
                .only('id', 'school_class_id', 'section_id')
                .filter(user=user)
                .first()
            )
            if profile is None:
                return TestAssignment.objects.none()
            return qs.filter(
                test__school_id=user.school_id,
                test__status=Test.Status.PUBLISHED,
            ).filter(
                Q(
                    assigned_to_type=TestAssignment.AssignedToType.CLASS,
                    school_class_id=profile.school_class_id,
                )
                | Q(
                    assigned_to_type=TestAssignment.AssignedToType.SECTION,
                    section_id=profile.section_id,
                )
                | Q(
                    assigned_to_type=TestAssignment.AssignedToType.STUDENTS,
                    student_assignments__student=user,
                )
            ).distinct()
        return TestAssignment.objects.none()

    def _require_manage_role(self) -> None:
        if self.request.user.role not in MANAGE_ROLES:
            raise PermissionDenied('Students cannot manage test assignments.')

    def create(self, request, *args, **kwargs):
        self._require_manage_role()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = serializer.save(assigned_by=request.user)

        # Fan out one in-app notification per eligible student. A notification
        # failure must never roll back the assignment itself.
        try:
            notified_count = create_assignment_notifications(assignment)
        except Exception:
            logger.exception(
                'Failed to create notifications for assignment %s', assignment.pk,
            )
            notified_count = 0

        log_action(
            request.user,
            AuditLog.Action.TEST_ASSIGNED,
            entity_type='TestAssignment',
            entity_id=assignment.pk,
            request=request,
            details={
                'test': assignment.test.title,
                'type': assignment.assigned_to_type,
                'students_notified': notified_count,
            },
        )

        out = TestAssignmentListSerializer(assignment).data
        out['students_notified'] = notified_count
        return Response(out, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        self._require_manage_role()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._require_manage_role()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._require_manage_role()
        return super().destroy(request, *args, **kwargs)
