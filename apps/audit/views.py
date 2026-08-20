"""Audit log API — read-only listing for CSC Admin and School Admin."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from .models import AuditLog
from .serializers import AuditLogSerializer

User = get_user_model()


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related('user').all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'action': ['exact'],
        'status': ['exact'],
        'user': ['exact'],
        'created_at': ['gte', 'lte'],
    }
    search_fields = ['user__email', 'user__student_id', 'entity_type', 'action']
    ordering_fields = ['created_at', 'action']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        if user.role not in ('csc_admin', 'school_admin'):
            raise PermissionDenied('Only admins can view audit logs.')
        qs = self.queryset
        if user.role == 'csc_admin':
            return qs
        return qs.filter(user__school_id=user.school_id)
