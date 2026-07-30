"""Schools API.

- CSC Admin: full CRUD on every school + platform-wide stats endpoint.
- School Admin: read-only access to own school + own school stats.
- Other roles: forbidden.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditLog
from apps.audit.services import log_action

from .models import School
from .serializers import (
    PlatformStatsSerializer,
    SchoolAdminSerializer,
    SchoolAdminWriteSerializer,
    SchoolListSerializer,
    SchoolSerializer,
    SchoolStatsSerializer,
)
from .services import create_school_with_admin, delete_school

User = get_user_model()
logger = logging.getLogger(__name__)


class SchoolViewSet(viewsets.ModelViewSet):
    """CRUD for schools, plus dashboard stats endpoints."""

    queryset = School.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'city', 'state']
    search_fields = ['name', 'code', 'city', 'official_email']
    ordering_fields = ['name', 'created_at', 'status']
    ordering = ['name']

    def get_serializer_class(self):
        if self.action == 'list':
            return SchoolListSerializer
        return SchoolSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return School.objects.none()
        qs = School.objects.all()
        if user.role == User.Role.CSC_ADMIN:
            pass
        elif user.school_id is not None:
            qs = qs.filter(pk=user.school_id)
        else:
            return School.objects.none()
        if self.action == 'list':
            # Active accounts only, matching platform-stats and the school detail stats.
            # Counting deactivated users here made this table disagree with every other
            # count in the product.
            active = Q(users__is_active=True)
            qs = qs.annotate(
                user_count=Count('users', filter=active),
                teachers_count=Count('users', filter=active & Q(users__role='teacher')),
                students_count=Count('users', filter=active & Q(users__role='student')),
            )
        return qs

    def _require_csc_admin(self):
        if self.request.user.role != User.Role.CSC_ADMIN:
            raise PermissionDenied('Only CSC Admin can perform this action.')

    def create(self, request, *args, **kwargs):
        self._require_csc_admin()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if request.user.role != User.Role.CSC_ADMIN:
            raise PermissionDenied('Only CSC Admin can modify schools.')
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if request.user.role != User.Role.CSC_ADMIN:
            raise PermissionDenied('Only CSC Admin can modify schools.')
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._require_csc_admin()
        instance = self.get_object()
        log_action(
            request.user,
            AuditLog.Action.SCHOOL_UPDATED,
            entity_type='School',
            entity_id=instance.id,
            request=request,
            details={'op': 'delete', 'code': instance.code, 'name': instance.name},
        )
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        """Route deletion through the service — a plain delete() hits PROTECTed rows."""
        delete_school(instance)

    def perform_create(self, serializer):
        """Create the School and its first School Admin as one atomic unit.

        The admin is provisioned WITHOUT a password; a one-time setup link is emailed to
        them and also returned to the CSC Admin (as `admin_setup_link`) so it can be handed
        over directly. Email delivery is best-effort — a mail failure must not undo the
        already-committed school, and the link stays valid regardless of delivery.
        """
        data = dict(serializer.validated_data)
        admin_full_name = data.pop('admin_full_name')
        admin_email = data.pop('admin_email')

        school, admin = create_school_with_admin(
            school_data=data,
            admin_full_name=admin_full_name,
            admin_email=admin_email,
            created_by=self.request.user,
        )
        # DRF returns serializer.instance in the response; the service bypassed
        # serializer.save(), so point it at the school we just created.
        serializer.instance = school

        # Build the link independently of the send so the CSC Admin still gets it even if
        # SMTP is down; the serializer surfaces it from this attribute on the response only.
        from apps.authentication.services import (
            EmailDeliveryError,
            build_password_setup_link,
            send_password_setup_email,
        )
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
        try:
            setup_link = send_password_setup_email(admin, frontend_url=frontend_url)
        except EmailDeliveryError:
            setup_link = build_password_setup_link(admin, frontend_url=frontend_url)
            logger.warning(
                'School Admin %s created but setup email failed to send; link returned to '
                'the CSC Admin instead.', admin.email,
            )
        school._admin_setup_link = setup_link

        log_action(
            self.request.user,
            AuditLog.Action.SCHOOL_CREATED,
            entity_type='School',
            entity_id=school.id,
            request=self.request,
            details={'code': school.code, 'name': school.name},
        )
        log_action(
            self.request.user,
            AuditLog.Action.USER_CREATED,
            entity_type='User',
            entity_id=admin.id,
            request=self.request,
            details={'role': 'school_admin', 'login_email': admin.email, 'school': school.code},
        )

    def perform_update(self, serializer):
        school = serializer.save()
        log_action(
            self.request.user,
            AuditLog.Action.SCHOOL_UPDATED,
            entity_type='School',
            entity_id=school.id,
            request=self.request,
            details={'code': school.code, 'name': school.name},
        )

    @action(detail=True, methods=['get'], url_path='admins')
    def admins(self, request, pk=None):
        """List this school's School Admin accounts (their LOGIN emails, not the school's)."""
        school = self.get_object()
        admins = User.objects.filter(
            school_id=school.id, role=User.Role.SCHOOL_ADMIN,
        ).order_by('full_name')
        return Response(SchoolAdminSerializer(admins, many=True).data)

    @action(detail=True, methods=['patch'], url_path='admins/(?P<admin_id>[^/.]+)')
    def update_admin(self, request, pk=None, admin_id=None):
        """Update a School Admin's account: name, login email, password, or active flag.

        Deliberately NOT part of PATCH /schools/{id}/ — changing a school's contact details
        must never be able to change who can log into it.
        """
        self._require_csc_admin()
        school = self.get_object()
        admin = User.objects.filter(
            pk=admin_id, school_id=school.id, role=User.Role.SCHOOL_ADMIN,
        ).first()
        if admin is None:
            return Response(
                {'detail': 'School Admin not found for this school.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SchoolAdminWriteSerializer(
            data=request.data, context={'request': request, 'instance': admin},
        )
        serializer.is_valid(raise_exception=True)
        admin = serializer.update(admin, serializer.validated_data)

        log_action(
            request.user,
            AuditLog.Action.USER_UPDATED,
            entity_type='User',
            entity_id=admin.id,
            request=request,
            details={
                'role': 'school_admin',
                'school': school.code,
                'password_reset': bool(serializer.validated_data.get('password')),
            },
        )
        return Response(SchoolAdminSerializer(admin).data)

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """Per-school aggregate counts. CSC Admin sees any school; school_admin only own."""
        school = self.get_object()
        # Avoid circular imports — pull related models lazily.
        from apps.academics.models import Class, Subject
        from apps.tests.models import Test

        # Count only active accounts — soft-delete (Deactivate) sets is_active=False
        # and those users must not inflate dashboard KPIs.
        users = User.objects.filter(school_id=school.id, is_active=True)
        data = {
            'school_admins': users.filter(role=User.Role.SCHOOL_ADMIN).count(),
            'teachers': users.filter(role=User.Role.TEACHER).count(),
            'students': users.filter(role=User.Role.STUDENT).count(),
            'classes': Class.objects.filter(school_id=school.id).count(),
            'subjects': Subject.objects.filter(school_id=school.id).count(),
            'tests': Test.objects.filter(school_id=school.id).count(),
        }
        return Response(SchoolStatsSerializer(data).data)

    @action(detail=False, methods=['get'], url_path='platform-stats')
    def platform_stats(self, request):
        """Platform-wide stats for the CSC Admin dashboard. CSC Admin only."""
        self._require_csc_admin()
        from apps.exams.models import ExamSession
        from apps.tests.models import Test

        today = timezone.localdate()
        # Active accounts only — deactivated users are excluded from platform KPIs.
        users = User.objects.filter(is_active=True)
        # "Total users" counts staff only: school admins + teachers. Students are
        # deliberately excluded — they have their own card, and they outnumber staff so
        # heavily that including them made this one just a restatement of the student
        # count. CSC/platform admins stay excluded as well (they belong to no school).
        staff_users = users.filter(
            role__in=(User.Role.SCHOOL_ADMIN, User.Role.TEACHER),
        )
        data = {
            'total_schools': School.objects.count(),
            'active_schools': School.objects.filter(status=School.Status.ACTIVE).count(),
            'total_users': staff_users.count(),
            'total_school_admins': users.filter(role=User.Role.SCHOOL_ADMIN).count(),
            'total_teachers': users.filter(role=User.Role.TEACHER).count(),
            'total_students': users.filter(role=User.Role.STUDENT).count(),
            'total_tests': Test.objects.count(),
            'exams_today': ExamSession.objects.filter(
                Q(started_at__date=today) | Q(submitted_at__date=today)
            ).distinct().count(),
        }
        return Response(PlatformStatsSerializer(data).data)
