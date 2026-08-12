"""ViewSet for the Question Bank.

Permission model:
- CSC Admin: full access across all schools.
- School Admin: full CRUD within own school.
- Teacher: full CRUD within own school.
- Student: no access.

Multi-tenant scoping is handled in ``get_queryset`` and ``perform_create``.
"""
from __future__ import annotations

import csv
import io
import logging

from django.db import transaction
from django.db.models import ProtectedError, Q
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from rest_framework.parsers import FormParser, MultiPartParser

from apps.common.pagination import LargePagination
from apps.teachers.models import TeacherAssignment

from .models import Question
from .serializers import (
    QuestionBulkDeleteSerializer,
    QuestionBulkImportSerializer,
    QuestionDetailSerializer,
    QuestionListSerializer,
    QuestionWriteSerializer,
)

logger = logging.getLogger(__name__)


class QuestionViewSet(viewsets.ModelViewSet):
    """CRUD for MCQ questions with school-scoped access."""

    permission_classes = [IsAuthenticated]
    # The global default is stock PageNumberPagination, whose page_size_query_param
    # is None — it SILENTLY IGNORES ?page_size and hard-caps every response at 20.
    # Both callers of this endpoint group questions client-side (the Question Bank
    # by subject, the test question picker by chapter), and a group split across an
    # invisible 20-row boundary is simply wrong, so both ask for a large page and
    # were until now getting 20 without any error. Same fix already applied to
    # SubjectViewSet and ChapterViewSet.
    pagination_class = LargePagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['school', 'subject', 'chapter', 'lesson', 'difficulty', 'is_active']
    search_fields = ['question_text']
    ordering_fields = ['created_at', 'difficulty', 'marks']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Question.objects.none()

        qs = Question.objects.select_related('subject', 'chapter', 'created_by')

        if user.role == 'student':
            return qs.none()
        if user.role == 'csc_admin':
            pass
        elif user.school_id is None:
            return qs.none()
        else:
            qs = qs.filter(school_id=user.school_id)

        # A teacher may only work with questions for the subjects assigned to them. Two shapes
        # of assignment exist (see TeacherAssignment): a subject-bound row grants exactly that
        # subject; a whole-class row (no subject named — what the Add Teacher form creates)
        # grants every subject of that class. Applied here (not just in the UI) so the Question
        # Bank, test creation, and counts all enforce the same limit for a teacher.
        if user.role == 'teacher':
            assignments = TeacherAssignment.objects.filter(teacher__user=user)
            whole_class_ids = assignments.filter(
                subject__isnull=True,
            ).values_list('school_class_id', flat=True)
            named_subject_ids = assignments.filter(
                subject__isnull=False,
            ).values_list('subject_id', flat=True)
            qs = qs.filter(
                Q(subject__school_class_id__in=whole_class_ids)
                | Q(subject_id__in=named_subject_ids)
            )

        # Hide archived/inactive questions from the bank by default so a
        # "deleted" (archived) question disappears from the active list. An
        # explicit ?is_active= filter overrides this for the Active/Inactive toggle.
        if self.action in ('list', 'count') and 'is_active' not in self.request.query_params:
            qs = qs.filter(is_active=True)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return QuestionListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return QuestionWriteSerializer
        if self.action == 'bulk_import':
            return QuestionBulkImportSerializer
        return QuestionDetailSerializer

    def _check_access(self) -> None:
        """Deny access if the user is a student."""
        if self.request.user.role == 'student':
            raise PermissionDenied('Students cannot manage questions.')

    def create(self, request: Request, *args, **kwargs) -> Response:
        self._check_access()
        return super().create(request, *args, **kwargs)

    def update(self, request: Request, *args, **kwargs) -> Response:
        self._check_access()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request: Request, *args, **kwargs) -> Response:
        self._check_access()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request: Request, *args, **kwargs) -> Response:
        self._check_access()
        instance = self.get_object()
        try:
            with transaction.atomic():
                instance.delete()
        except ProtectedError:
            # The question is referenced by a test / exam / result (PROTECT FKs).
            # Hard-deleting would destroy exam history, so archive it instead.
            instance.is_active = False
            instance.save(update_fields=['is_active'])
            logger.info('Question %s archived (in use) instead of hard delete.', instance.pk)
            return Response(
                {
                    'archived': True,
                    'detail': (
                        'This question is used in existing tests or exams, so it was '
                        'archived instead of being permanently deleted.'
                    ),
                },
                status=status.HTTP_200_OK,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_create(self, serializer) -> None:
        user = self.request.user
        if user.role == 'csc_admin':
            school_id = self.request.data.get('school')
            if not school_id:
                raise PermissionDenied('CSC Admin must provide `school` when creating a question.')
            serializer.save(school_id=school_id, created_by=user)
        else:
            serializer.save(school=user.school, created_by=user)

    @action(
        detail=False,
        methods=['get'],
        url_path='import-template',
        permission_classes=[IsAuthenticated],
    )
    def import_template(self, request: Request) -> HttpResponse:
        """Download a CSV template with the expected columns + 2 example rows.

        The subject column carries the Subject ID (e.g. KA_MAT_10), which replaced the
        old subject_name column — names repeat across classes, Subject IDs do not. The
        example rows use real Subject IDs from the downloader's own school where
        available, so the template can be filled in and uploaded without edits.
        """
        from apps.academics.models import Subject

        output = io.StringIO()
        writer = csv.writer(output)

        # Real Subject IDs from this user's school so the examples are directly usable.
        # CSC Admin has no school of their own — they fall back to the generic samples.
        example_codes = []
        if request.user.school_id:
            example_codes = list(
                Subject.objects.filter(
                    school_id=request.user.school_id, is_active=True,
                ).exclude(code='').order_by('code').values_list('code', flat=True)[:2]
            )
        subject_1 = example_codes[0] if example_codes else 'KA_MAT_10'
        subject_2 = example_codes[1] if len(example_codes) > 1 else subject_1

        # Header row — required first, then optional
        writer.writerow([
            'question_text', 'option_a', 'option_b', 'option_c', 'option_d',
            'correct_option', 'subject_id', 'chapter_name', 'lesson', 'difficulty',
            'marks', 'explanation', 'negative_marks',
        ])
        # Example row 1
        writer.writerow([
            'What is 2 + 2?', '3', '4', '5', '6',
            'b', subject_1, 'Basic Arithmetic', 'Addition Intro', 'easy', '1',
            'Basic addition', '0',
        ])
        # Example row 2
        writer.writerow([
            'What is the capital of Tamil Nadu?',
            'Chennai', 'Coimbatore', 'Madurai', 'Salem',
            'a', subject_2, 'Indian States', 'State Capitals', 'easy', '1',
            'Chennai is the capital of Tamil Nadu', '0',
        ])

        response = HttpResponse(output.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = (
            'attachment; filename="question_import_template.csv"'
        )
        return response

    @action(detail=False, methods=['post'], url_path='bulk-delete')
    def bulk_delete(self, request: Request) -> Response:
        """Delete several questions at once.

        Same semantics as ``destroy``, applied per question: one that is referenced
        by a test/exam is archived instead of hard-deleted so exam history survives.

        IDs are filtered through ``get_queryset`` so a caller can never touch another
        school's questions by guessing primary keys. Unknown/out-of-scope IDs are
        reported in ``skipped`` rather than failing the whole request.
        """
        self._check_access()

        serializer = QuestionBulkDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data['ids']

        # School-scoped: `get_queryset` applies the tenant filter. Its is_active
        # filter only applies to `list`/`count`, so archived questions are
        # deletable here too.
        questions = list(self.get_queryset().filter(id__in=ids))
        found_ids = {q.pk for q in questions}
        skipped = [i for i in ids if i not in found_ids]

        # Identify referenced questions using bulk queries to avoid loop overhead.
        from apps.tests.models import TestQuestion
        from apps.exams.models import ExamAnswer
        from apps.results.models import ResultDetail

        referenced_ids = set()

        # Check references in TestQuestion
        test_q_ids = TestQuestion.objects.filter(question_id__in=found_ids).values_list('question_id', flat=True).distinct()
        referenced_ids.update(test_q_ids)

        # Check references in ExamAnswer
        exam_a_ids = ExamAnswer.objects.filter(question_id__in=found_ids).values_list('question_id', flat=True).distinct()
        referenced_ids.update(exam_a_ids)

        # Check references in ResultDetail
        result_d_ids = ResultDetail.objects.filter(question_id__in=found_ids).values_list('question_id', flat=True).distinct()
        referenced_ids.update(result_d_ids)

        # Soft delete/archive the referenced questions
        archived = len(referenced_ids)
        if referenced_ids:
            Question.objects.filter(id__in=referenced_ids).update(is_active=False)

        # Hard delete the unreferenced questions
        delete_ids = found_ids - referenced_ids
        deleted = len(delete_ids)
        if delete_ids:
            Question.objects.filter(id__in=delete_ids).delete()

        return Response(
            {'deleted': deleted, 'archived': archived, 'skipped': len(skipped)},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='count')
    def count(self, request: Request) -> Response:
        """Return count of questions matching the current filters."""
        qs = self.filter_queryset(self.get_queryset())
        return Response({'count': qs.count()}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=['post'],
        url_path='bulk-import',
        parser_classes=[MultiPartParser, FormParser],
    )
    def bulk_import(self, request: Request) -> Response:
        """Bulk import questions from a CSV or Excel file."""
        self._check_access()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(result, status=status.HTTP_200_OK)
