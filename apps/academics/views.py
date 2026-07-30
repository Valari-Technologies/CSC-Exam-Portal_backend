"""ViewSets for Class, Section, Subject, Chapter.

Permission model:
- CSC Admin: full access across all schools.
- School Admin: full CRUD within own school.
- Teacher: read-only within own school.
- Student: no access.

Multi-tenant scoping is handled in `get_queryset` and `perform_create`.
"""
from __future__ import annotations

from django.db.models import Count, Max
from django.db.models.deletion import ProtectedError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import LargePagination

from .models import Chapter, Class, Section, Subject
from .serializers import (
    ChapterSerializer,
    ClassSerializer,
    SectionSerializer,
    SubjectSerializer,
)
from .services import generate_subject_id


class _SchoolScopedViewSet(viewsets.ModelViewSet):
    """Base ViewSet enforcing CSC Admin / School Admin write, Teacher read-only.

    Subclasses must set `school_lookup` (a path back to `school_id`) for filtering.
    """

    permission_classes = [IsAuthenticated]
    school_lookup = 'school_id'

    def _is_writer(self) -> bool:
        return self.request.user.role in ('csc_admin', 'school_admin')

    def _check_write_permission(self) -> None:
        if not self._is_writer():
            raise PermissionDenied('You do not have permission to modify academic structure.')

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return self.queryset.none()
        if user.role == 'student':
            return self.queryset.none()
        if user.role == 'csc_admin':
            return self.queryset.all()
        if user.school_id is None:
            return self.queryset.none()
        return self.queryset.filter(**{self.school_lookup: user.school_id})

    def create(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._check_write_permission()
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError as exc:
            # Hard delete is blocked by PROTECT FKs (tests, exam attempts,
            # results, enrolled students, ...). Historical exam data must stay
            # intact, so return a controlled 400 — never a 500 — and point the
            # user at soft delete (Deactivate) instead.
            resource = self.queryset.model._meta.verbose_name
            blockers = sorted({
                str(obj._meta.verbose_name_plural) for obj in exc.protected_objects
            })
            return Response(
                {
                    'detail': (
                        f'Cannot delete this {resource} because it is linked to existing '
                        f'records ({", ".join(blockers)}). '
                        f'Use Deactivate instead to hide it while keeping historical '
                        f'exam data and reports intact.'
                    ),
                    'blocked_by': blockers,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


class ClassViewSet(_SchoolScopedViewSet):
    queryset = Class.objects.all()
    serializer_class = ClassSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'school']
    search_fields = ['name']
    ordering_fields = ['numeric_value', 'name', 'created_at']
    ordering = ['numeric_value', 'name']

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                sections_count=Count('sections', distinct=True),
                subjects_count=Count('subjects', distinct=True),
            )
        )

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == 'csc_admin':
            school_id = self.request.data.get('school')
            if not school_id:
                raise PermissionDenied('CSC Admin must provide `school` when creating a class.')
            serializer.save(school_id=school_id)
        else:
            serializer.save(school=user.school)


class SectionViewSet(_SchoolScopedViewSet):
    queryset = Section.objects.select_related('school_class').all()
    serializer_class = SectionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'school', 'school_class']
    search_fields = ['name', 'school_class__name']
    ordering_fields = ['name', 'created_at']

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(student_count=Count('students', distinct=True))
        )

    def perform_create(self, serializer):
        school_class: Class = serializer.validated_data['school_class']
        serializer.save(school_id=school_class.school_id)


class SubjectViewSet(_SchoolScopedViewSet):
    queryset = Subject.objects.select_related('school_class').all()
    serializer_class = SubjectSerializer
    # A school can have many subjects (e.g. ~5 per class across 10 classes). The stock
    # PageNumberPagination ignores ?page_size= and caps at 20, which truncated the New
    # Chapter dialog's subject list to the first few classes. LargePagination honours
    # page_size (up to 500) so callers requesting the full list get every subject.
    pagination_class = LargePagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'school', 'school_class']
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'created_at']
    # List subjects in creation order (oldest first, newest appended at the end).
    # `id` is the tiebreaker so bulk-seeded rows sharing a created_at timestamp still
    # sort deterministically — keeping the order stable across refreshes and every
    # place the subject list is shown.
    ordering = ['created_at', 'id']

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                chapter_count=Count('chapters', distinct=True),
                question_count=Count('questions', distinct=True),
            )
        )

    def perform_create(self, serializer):
        school_class: Class = serializer.validated_data['school_class']
        # The Subject ID (code) is server-generated from the school, subject name and class.
        code = generate_subject_id(
            school_class.school, serializer.validated_data['name'], school_class.numeric_value,
        )
        serializer.save(school_id=school_class.school_id, code=code)

    def perform_update(self, serializer):
        # Keep the Subject ID in sync only when the inputs that shape it change (name or
        # class). A status-only PATCH (or any other edit) leaves the existing ID untouched.
        instance: Subject = serializer.instance
        new_name = serializer.validated_data.get('name', instance.name)
        new_class = serializer.validated_data.get('school_class', instance.school_class)
        if new_name != instance.name or new_class != instance.school_class:
            code = generate_subject_id(
                new_class.school, new_name, new_class.numeric_value, exclude_pk=instance.pk,
            )
            serializer.save(code=code)
        else:
            serializer.save()


class ChapterViewSet(_SchoolScopedViewSet):
    queryset = Chapter.objects.select_related('subject').all()
    serializer_class = ChapterSerializer
    # A subject can have many chapters; honour ?page_size so the list isn't capped at 20
    # (which, with creation ordering below, would hide newly created chapters on page 2).
    pagination_class = LargePagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'subject']
    search_fields = ['name']
    ordering_fields = ['order_number', 'name', 'created_at']
    # List chapters in creation order (oldest first, newest appended at the end); `id`
    # is the tiebreaker so the order stays stable across refreshes and everywhere shown.
    ordering = ['created_at', 'id']
    school_lookup = 'subject__school_id'

    def get_queryset(self):
        # Chapter has no direct `school` FK; honor an optional ?school= filter
        # (used by the CSC Admin school selector) via the subject relation.
        qs = super().get_queryset()
        school = self.request.query_params.get('school')
        if school:
            qs = qs.filter(subject__school_id=school)
        return qs

    def _is_writer(self) -> bool:
        # Teachers may manage chapters — create, edit, activate/deactivate, and delete —
        # each action further narrowed to their assigned subjects (perform_create,
        # perform_update, destroy). Admins remain unrestricted within their school.
        if self.action in (
            'create', 'update', 'partial_update', 'destroy'
        ) and self.request.user.role == 'teacher':
            return True
        return super()._is_writer()

    def perform_create(self, serializer):
        subject = serializer.validated_data['subject']
        self._assert_subject_allowed(subject)
        # order_number is no longer entered in the New Chapter form. Auto-assign the next
        # value for the subject so the "Ch.N" label (used e.g. in the question form) stays
        # sequential and meaningful instead of every chapter defaulting to 1.
        next_order = (
            Chapter.objects.filter(subject=subject).aggregate(m=Max('order_number'))['m'] or 0
        ) + 1
        serializer.save(order_number=next_order)

    def perform_update(self, serializer):
        # A teacher may only edit (or activate/deactivate via PATCH) chapters within their
        # assigned subject scope — both the chapter's current subject and, if it is being
        # reassigned, the target subject. Admins skip these checks inside _assert_*.
        self._assert_subject_allowed(serializer.instance.subject)
        new_subject = serializer.validated_data.get('subject')
        if new_subject is not None:
            self._assert_subject_allowed(new_subject)
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        # Enforce the teacher's subject scope on the target chapter before the base
        # destroy runs (which handles admin write-permission + ProtectedError → 400).
        if request.user.role == 'teacher':
            self._assert_subject_allowed(self.get_object().subject)
        return super().destroy(request, *args, **kwargs)

    def _assert_subject_allowed(self, subject: Subject) -> None:
        """A teacher may only manage chapters for subjects assigned to them.

        Two assignment shapes are honoured (mirrors QuestionViewSet.get_queryset): a
        subject-bound assignment grants exactly that subject; a whole-class assignment
        (no subject named — what the Add Teacher form creates) grants every subject of
        that class. Admins are unrestricted here — the serializer's validate_subject
        already keeps them within their own school.
        """
        user = self.request.user
        if user.role != 'teacher':
            return
        # Imported here rather than at module load: apps.teachers.models imports
        # apps.academics.models, so a top-level import would risk a circular import.
        from apps.teachers.models import TeacherAssignment

        assignments = TeacherAssignment.objects.filter(teacher__user=user)
        whole_class_ids = set(
            assignments.filter(subject__isnull=True).values_list('school_class_id', flat=True)
        )
        named_subject_ids = set(
            assignments.filter(subject__isnull=False).values_list('subject_id', flat=True)
        )
        if subject.id not in named_subject_ids and subject.school_class_id not in whole_class_ids:
            raise PermissionDenied('You can only manage chapters for subjects assigned to you.')
