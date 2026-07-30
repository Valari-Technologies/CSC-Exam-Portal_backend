"""Teacher profile + assignment API.

Permission model:
- CSC Admin: full access across all schools.
- School Admin: full CRUD within own school.
- Teacher: read own profile only.
- Student: no access.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.services import log_action
from apps.audit.models import AuditLog

from .models import TeacherAssignment, TeacherProfile
from .serializers import (
    TeacherAssignmentReadSerializer,
    TeacherAssignmentWriteSerializer,
    TeacherProfileDetailSerializer,
    TeacherProfileListSerializer,
    TeacherProfileWriteSerializer,
    TeacherStatsSerializer,
)

User = get_user_model()


class TeacherProfileViewSet(viewsets.ModelViewSet):
    queryset = TeacherProfile.objects.select_related('user', 'school').all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'school']
    search_fields = ['user__full_name', 'user__email', 'teacher_id', 'employee_id']
    ordering_fields = ['user__full_name', 'joining_date', 'user__created_at']
    ordering = ['user__full_name']

    def get_serializer_class(self):
        if self.action == 'list':
            return TeacherProfileListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return TeacherProfileWriteSerializer
        return TeacherProfileDetailSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return TeacherProfile.objects.none()
        if user.role == 'student':
            return TeacherProfile.objects.none()
        qs = self.queryset
        if user.role == 'csc_admin':
            pass
        elif user.role == 'teacher':
            qs = qs.filter(user=user)
        elif user.school_id:
            qs = qs.filter(school_id=user.school_id)
        else:
            return TeacherProfile.objects.none()

        if self.action == 'list':
            qs = qs.annotate(assignments_count=Count('assignments', distinct=True))
        return qs

    def _require_admin(self):
        if self.request.user.role not in ('csc_admin', 'school_admin'):
            raise PermissionDenied('Only admins can perform this action.')

    @action(detail=False, methods=['get'], url_path='my-stats')
    def my_stats(self, request):
        """Counters for the requesting teacher's own dashboard.

        `assigned_classes` counts this teacher's DISTINCT (class, section) assignments — so
        "Class 8-A, Class 9-B, Class 10-C" is 3, and a teacher holding both 8-A and 8-B has
        2. It is never the school's class total. Distinct on the pair rather than a plain
        row count so a duplicate subject-bound row can't inflate the card.

        `assigned_subjects` counts the DISTINCT subjects this teacher actually teaches.
        Nulls are excluded: a class/section assignment made before any subject was chosen
        carries no subject, and counting it would report a subject that doesn't exist.

        Scoped to `teacher__user=request.user`, so a teacher only ever sees their own.
        """
        if request.user.role != User.Role.TEACHER:
            raise PermissionDenied('Only teachers have personal teaching stats.')

        my_assignments = TeacherAssignment.objects.filter(teacher__user=request.user)

        assigned_subjects = (
            my_assignments
            .filter(subject__isnull=False)
            .values('subject')
            .distinct()
            .count()
        )

        assigned_classes = (
            my_assignments
            .values('school_class', 'section')
            .distinct()
            .count()
        )
        return Response(TeacherStatsSerializer({
            'assigned_classes': assigned_classes,
            'assigned_subjects': assigned_subjects,
        }).data)

    @action(detail=False, methods=['get'], url_path='my-assignments')
    def my_assignments(self, request):
        """The DISTINCT classes and section letters the requesting teacher is assigned.

        Backs the Class/Section filters on Teacher -> Students, which must offer only what the
        School Admin assigned this teacher — never the whole school's classes/sections (item 2).

        A whole-class assignment (section is null) means every section of that class, so its
        section letters are expanded from the class's own sections. The student list itself is
        already scoped server-side; this just keeps the filter options honest.
        """
        if request.user.role != User.Role.TEACHER:
            raise PermissionDenied('Only teachers have personal assignments.')

        from apps.academics.models import Section

        assignments = (
            TeacherAssignment.objects
            .filter(teacher__user=request.user)
            .select_related('school_class', 'section')
        )

        classes: dict[int, str] = {}
        section_names: set[str] = set()
        whole_class_ids: set[int] = set()
        for assignment in assignments:
            if assignment.school_class_id:
                classes[assignment.school_class_id] = assignment.school_class.name
            if assignment.section_id:
                section_names.add(assignment.section.name)
            elif assignment.school_class_id:
                # section is null -> the teacher covers every section of this class.
                whole_class_ids.add(assignment.school_class_id)

        if whole_class_ids:
            section_names.update(
                Section.objects
                .filter(school_class_id__in=whole_class_ids)
                .values_list('name', flat=True)
            )

        return Response({
            'classes': [
                {'id': class_id, 'name': name}
                for class_id, name in sorted(classes.items(), key=lambda kv: kv[1])
            ],
            'sections': sorted(section_names),
        })

    def create(self, request, *args, **kwargs):
        self._require_admin()
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        profile = serializer.create(serializer.validated_data)
        log_action(
            request.user,
            AuditLog.Action.USER_CREATED,
            entity_type='TeacherProfile',
            entity_id=profile.id,
            request=request,
            details={'email': profile.user.email, 'name': profile.user.full_name},
        )
        out = TeacherProfileDetailSerializer(profile).data
        return Response(out, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        self._require_admin()
        instance = self.get_object()
        serializer = self.get_serializer(
            data=request.data,
            context={'request': request, 'instance': instance},
        )
        serializer.is_valid(raise_exception=True)
        profile = serializer.update(instance, serializer.validated_data)
        log_action(
            request.user,
            AuditLog.Action.USER_UPDATED,
            entity_type='TeacherProfile',
            entity_id=profile.id,
            request=request,
            details={'email': profile.user.email},
        )
        out = TeacherProfileDetailSerializer(profile).data
        return Response(out)

    def partial_update(self, request, *args, **kwargs):
        self._require_admin()
        instance = self.get_object()
        serializer = self.get_serializer(
            data=request.data,
            partial=True,
            context={'request': request, 'instance': instance},
        )
        serializer.is_valid(raise_exception=True)
        profile = serializer.update(instance, serializer.validated_data)
        out = TeacherProfileDetailSerializer(profile).data
        return Response(out)

    def destroy(self, request, *args, **kwargs):
        self._require_admin()
        instance = self.get_object()
        user_obj = instance.user
        log_action(
            request.user,
            AuditLog.Action.USER_DEACTIVATED,
            entity_type='TeacherProfile',
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
        """Permanently delete the teacher: User + profile + assignments. Irreversible."""
        self._require_admin()
        instance = self.get_object()
        user_obj = instance.user
        log_action(
            request.user,
            AuditLog.Action.USER_DEACTIVATED,
            entity_type='TeacherProfile',
            entity_id=instance.id,
            request=request,
            details={'email': user_obj.email, 'op': 'hard_delete'},
        )
        # Deleting the User cascades to TeacherProfile and TeacherAssignment.
        user_obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeacherAssignmentViewSet(viewsets.ModelViewSet):
    queryset = TeacherAssignment.objects.select_related(
        'teacher__user', 'subject', 'school_class', 'section',
    ).all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['teacher', 'school_class', 'subject', 'academic_year']
    ordering = ['-assigned_at']

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return TeacherAssignmentWriteSerializer
        return TeacherAssignmentReadSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return TeacherAssignment.objects.none()
        if user.role == 'student':
            return TeacherAssignment.objects.none()
        qs = self.queryset
        if user.role == 'csc_admin':
            pass
        elif user.role == 'teacher':
            qs = qs.filter(teacher__user=user)
        elif user.school_id:
            qs = qs.filter(teacher__school_id=user.school_id)
        else:
            return TeacherAssignment.objects.none()
        return qs

    def _require_admin(self):
        if self.request.user.role not in ('csc_admin', 'school_admin'):
            raise PermissionDenied('Only admins can manage assignments.')

    def create(self, request, *args, **kwargs):
        self._require_admin()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._require_admin()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._require_admin()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._require_admin()
        return super().destroy(request, *args, **kwargs)
