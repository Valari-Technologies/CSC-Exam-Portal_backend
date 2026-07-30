"""Student profile + bulk import API.

Permission model:
- CSC Admin: full access across all schools.
- School Admin: full CRUD within own school.
- Teacher: sees and may EDIT only the students in the class/section combinations a School
  Admin has assigned them. Create, deactivate and delete stay admin-only.
- Student: read own profile only.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import StreamingHttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.schools.models import School

from .import_template import build_csv_template, build_xlsx_template
from .models import BulkImportLog, StudentProfile
from .serializers import (
    BulkImportLogSerializer,
    BulkImportUploadSerializer,
    StudentProfileDetailSerializer,
    StudentProfileListSerializer,
    StudentProfileWriteSerializer,
)
from .services import generate_student_id

User = get_user_model()


class StudentProfileViewSet(viewsets.ModelViewSet):
    queryset = StudentProfile.objects.select_related(
        'user', 'school', 'school_class', 'section',
    ).all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    # `section__name` lets the list be filtered by section LETTER (A-F) rather than by a
    # section PK. Sections are per-class rows, so a PK filter is only usable once a class
    # is chosen; the letter works on its own ("every Section B student") and still ANDs
    # with school_class for the narrower "Class 10, Section B". It composes with the
    # teacher scope below rather than replacing it — a teacher filtering by B still only
    # sees the B they are assigned.
    # `user__student_id` is an EXACT lookup, deliberately separate from `search` above,
    # which matches Student IDs by substring. Assigning a test to a named student is a
    # case where "close enough" is wrong: searching CSC001-001 would also return
    # CSC001-0010, and picking the wrong one sends the paper to the wrong child. The
    # Assign Test page resolves one typed ID to exactly one student through this.
    filterset_fields = [
        'is_active', 'school', 'school_class', 'section', 'section__name', 'user__student_id',
    ]
    search_fields = [
        'user__full_name', 'user__email', 'user__student_id', 'roll_number', 'admission_number',
    ]
    ordering_fields = ['user__full_name', 'roll_number', 'enrollment_date']
    ordering = ['school_class', 'section', 'roll_number']

    def get_serializer_class(self):
        if self.action == 'list':
            return StudentProfileListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return StudentProfileWriteSerializer
        return StudentProfileDetailSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return StudentProfile.objects.none()
        qs = self.queryset
        if user.role == 'csc_admin':
            pass
        elif user.role == 'student':
            qs = qs.filter(user=user)
        elif user.school_id:
            qs = qs.filter(school_id=user.school_id)
        else:
            return StudentProfile.objects.none()

        # A teacher sees only the class/section combinations assigned to them. This is the
        # single place teacher scope is decided: list, retrieve and edit all reach the
        # database through here, so an unassigned student is simply not found. A teacher
        # with no assignments sees nobody — which is what makes freshly imported students
        # invisible until a School Admin assigns their class/section to someone.
        #
        # ONE exception: an EXACT Student-ID lookup (Assign Test → Specific Students) may
        # resolve ANY student in the teacher's own school, not just their assigned sections.
        # That feature assigns to named recipients regardless of class, and the assignment
        # API already accepts any same-school student as a recipient — so scoping the lookup
        # to the teacher's sections was the bug that made students in other sections (e.g.
        # bulk-imported ones) unselectable. The lookup is exact (one student by full ID), so
        # this exposes no browsable roster; the school filter above still applies.
        if user.role == 'teacher':
            exact_id_lookup = (
                self.action == 'list'
                and bool(self.request.query_params.get('user__student_id'))
            )
            if not exact_id_lookup:
                qs = qs.filter(self._teacher_scope_q(user))
        return qs

    def _require_admin(self):
        if self.request.user.role not in ('csc_admin', 'school_admin'):
            raise PermissionDenied('Only admins can perform this action.')

    def _teacher_scope_q(self, user) -> Q:
        """Which students a teacher manages, as a queryset filter.

        Scope is the CLASS + SECTION named on their TeacherAssignment rows: a teacher
        assigned Class 10 Section A manages 10-A and nothing else, not the rest of Class 10.
        TeacherAssignment is the School Admin's "assign students to a teacher" mechanism —
        students exist independently of it (bulk import creates them with a class and
        section), and only become visible to a teacher once an assignment covers them.

        Two assignment shapes are honoured, per the model's own contract:
        - section named  -> exactly that section
        - section null   -> every section of that class

        Subject is irrelevant here. It scopes questions and chapters, not who a teacher
        manages as students, so a subject-bound row grants its class/section like any other.
        """
        # Imported here, not at module level: apps.teachers imports apps.students, so a
        # top-level import would be circular.
        from apps.teachers.models import TeacherAssignment

        pairs = TeacherAssignment.objects.filter(teacher__user=user).values_list(
            'school_class_id', 'section_id',
        )
        whole_class_ids = {c for c, s in pairs if s is None}
        section_ids = {s for _, s in pairs if s is not None}

        if not whole_class_ids and not section_ids:
            # No assignments at all — match nothing. An empty Q() would match everything.
            return Q(pk__in=[])

        return Q(school_class_id__in=whole_class_ids) | Q(section_id__in=section_ids)

    def _assert_can_edit(self, instance: StudentProfile) -> None:
        """Admins edit anyone in scope; a teacher only their assigned class/sections.

        In practice get_queryset has already 404'd anything out of scope before this runs.
        It is kept as a second, explicit gate so the rule survives a future change to how
        the object is fetched — and it reuses `_teacher_scope_q`, so the two can't drift.
        """
        user = self.request.user
        if user.role in ('csc_admin', 'school_admin'):
            return
        if user.role != 'teacher':
            raise PermissionDenied('Only admins can perform this action.')

        in_scope = StudentProfile.objects.filter(
            self._teacher_scope_q(user), pk=instance.pk,
        ).exists()
        if not in_scope:
            raise PermissionDenied(
                'You can only edit students in the classes and sections assigned to you.'
            )

    def create(self, request, *args, **kwargs):
        self._require_admin()
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        profile = serializer.create(serializer.validated_data)
        log_action(
            request.user,
            AuditLog.Action.USER_CREATED,
            entity_type='StudentProfile',
            entity_id=profile.id,
            request=request,
            details={'email': profile.user.email, 'name': profile.user.full_name},
        )
        out = StudentProfileDetailSerializer(profile).data
        return Response(out, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        # Scope depends on which student is being edited, so the check runs after
        # get_object() (which has already 404'd anything outside the caller's school).
        instance = self.get_object()
        self._assert_can_edit(instance)
        serializer = self.get_serializer(
            data=request.data,
            context={'request': request, 'instance': instance},
        )
        serializer.is_valid(raise_exception=True)
        profile = serializer.update(instance, serializer.validated_data)
        log_action(
            request.user,
            AuditLog.Action.USER_UPDATED,
            entity_type='StudentProfile',
            entity_id=profile.id,
            request=request,
            details={'email': profile.user.email},
        )
        out = StudentProfileDetailSerializer(profile).data
        return Response(out)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        self._assert_can_edit(instance)
        serializer = self.get_serializer(
            data=request.data,
            partial=True,
            context={'request': request, 'instance': instance},
        )
        serializer.is_valid(raise_exception=True)
        profile = serializer.update(instance, serializer.validated_data)
        out = StudentProfileDetailSerializer(profile).data
        return Response(out)

    def destroy(self, request, *args, **kwargs):
        self._require_admin()
        instance = self.get_object()
        user_obj = instance.user
        log_action(
            request.user,
            AuditLog.Action.USER_DEACTIVATED,
            entity_type='StudentProfile',
            entity_id=instance.id,
            request=request,
            details={'email': user_obj.email, 'op': 'delete'},
        )
        user_obj.is_active = False
        user_obj.save(update_fields=['is_active'])
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['delete'], url_path='hard-delete')
    def hard_delete(self, request, *args, **kwargs):
        """Permanently delete the student: User + profile + related data. Irreversible."""
        self._require_admin()
        instance = self.get_object()
        user_obj = instance.user
        log_action(
            request.user,
            AuditLog.Action.USER_DEACTIVATED,
            entity_type='StudentProfile',
            entity_id=instance.id,
            request=request,
            details={'email': user_obj.email, 'op': 'hard_delete'},
        )
        # Deleting the User cascades to StudentProfile and related records.
        user_obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'], url_path='next-student-id')
    def next_student_id(self, request):
        """Suggest the next free Student ID for a school.

        Backs the 'Generate' button on the student form. It has to be server-side: only
        the database knows which IDs are already taken. The value is a suggestion, not a
        reservation — create() re-generates if the admin submits it blank, and the unique
        constraint is the real guard against a collision.
        """
        self._require_admin()
        user = request.user

        if user.role == 'csc_admin':
            school_id = request.query_params.get('school')
            if not school_id:
                return Response(
                    {'detail': 'school query param is required for CSC Admin.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            school = School.objects.filter(pk=school_id).first()
            if school is None:
                return Response({'detail': 'School not found.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            school = user.school
            if school is None:
                return Response(
                    {'detail': 'Your account is not attached to a school.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response({'student_id': generate_student_id(school)})

    @action(
        detail=False,
        methods=['post'],
        url_path='bulk-import',
        parser_classes=[parsers.MultiPartParser, parsers.FormParser],
    )
    def bulk_import(self, request):
        """Upload a CSV to create students in bulk."""
        self._require_admin()
        serializer = BulkImportUploadSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        import_log = serializer.create(serializer.validated_data)
        log_action(
            request.user,
            AuditLog.Action.BULK_IMPORT,
            entity_type='BulkImportLog',
            entity_id=import_log.id,
            request=request,
            details={
                'file': import_log.file_name,
                'total': import_log.total_rows,
                'success': import_log.success_count,
                'fail': import_log.fail_count,
            },
        )
        out = BulkImportLogSerializer(import_log).data
        return Response(out, status=status.HTTP_201_CREATED)

    @action(
        detail=False,
        methods=['get'],
        url_path='import-template',
        permission_classes=[IsAuthenticated],
    )
    def import_template(self, request):
        """Download a blank import file in the exact format the importer accepts.

        Generated from the same column definitions the importer validates against, so a
        template downloaded here cannot be rejected for its headers.
        """
        self._require_admin()
        # Deliberately not called `format`: DRF reserves that query param for content
        # negotiation, and using it here makes the endpoint 404 looking for a "csv" renderer.
        fmt = (request.query_params.get('file_format') or 'xlsx').lower()
        if fmt not in ('xlsx', 'csv'):
            return Response(
                {'detail': 'file_format must be xlsx or csv.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if fmt == 'csv':
            content = build_csv_template()
            content_type = 'text/csv'
            filename = 'student_import_template.csv'
        else:
            content = build_xlsx_template()
            content_type = (
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            filename = 'student_import_template.xlsx'

        response = StreamingHttpResponse(iter([content]), content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Content-Length'] = str(len(content))
        return response

    def _import_logs_queryset(self):
        """Import logs the requesting user is allowed to see — school-scoped."""
        user = self.request.user
        qs = BulkImportLog.objects.all()
        if user.role != 'csc_admin':
            qs = qs.filter(school_id=user.school_id)
        return qs

    @action(detail=False, methods=['get'], url_path='import-logs')
    def import_logs(self, request):
        """List past import logs for this school."""
        self._require_admin()
        qs = self._import_logs_queryset()
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = BulkImportLogSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = BulkImportLogSerializer(qs, many=True)
        return Response(serializer.data)

    @action(
        detail=False,
        methods=['delete'],
        url_path=r'import-logs/(?P<log_id>\d+)',
    )
    def delete_import_log(self, request, log_id=None):
        """Delete one import history record.

        This removes the *record of* an import, never the students it created — those are
        ordinary StudentProfile rows with no dependency on this log. Scoped through
        _import_logs_queryset so a School Admin cannot delete another school's history.
        """
        self._require_admin()
        import_log = self._import_logs_queryset().filter(pk=log_id).first()
        if import_log is None:
            return Response(
                {'detail': 'Import record not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        log_action(
            request.user,
            AuditLog.Action.BULK_IMPORT_DELETED,
            entity_type='BulkImportLog',
            entity_id=import_log.id,
            request=request,
            details={
                'file': import_log.file_name,
                'imported_students': import_log.success_count,
                'note': 'Import history record deleted; imported students are unaffected.',
            },
        )
        import_log.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
