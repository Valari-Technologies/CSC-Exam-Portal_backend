"""Support-request endpoints — the Additional Details workflow.

School Admin: create a request, list/read their own school's requests.
Super Admin (CSC Admin): list/read every request, and reply (optionally resolving it).

Every notification here is in-app only. Nothing in this module sends email or reads/writes
School.official_email.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import IsCSCAdmin, IsCSCOrSchoolAdmin, IsSchoolAdmin
from apps.notifications.models import Notification
from apps.notifications.services import notify_user, notify_users

from .models import SupportRequest
from .serializers import SupportRequestReplySerializer, SupportRequestSerializer

User = get_user_model()
logger = logging.getLogger(__name__)


class SupportRequestViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Additional Details support requests.

    Scope is enforced by the queryset: a School Admin can only ever see their own school's
    requests, and only a School Admin can create one. The reply action is CSC-Admin only.
    """

    serializer_class = SupportRequestSerializer

    def get_permissions(self):
        if self.action == 'create':
            return [IsSchoolAdmin()]
        if self.action == 'reply':
            return [IsCSCAdmin()]
        return [IsCSCOrSchoolAdmin()]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return SupportRequest.objects.none()
        qs = SupportRequest.objects.select_related(
            'school', 'raised_by', 'resolved_by',
        )
        if user.role == 'school_admin':
            qs = qs.filter(school_id=user.school_id)
        # CSC Admin sees everything, filterable by status / school.
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        school_param = self.request.query_params.get('school')
        if school_param:
            qs = qs.filter(school_id=school_param)
        return qs

    def perform_create(self, serializer: SupportRequestSerializer) -> None:
        user = self.request.user
        support_request = serializer.save(school=user.school, raised_by=user)
        self._notify_super_admins(support_request)

    def _notify_super_admins(self, support_request: SupportRequest) -> None:
        """Tell every active Super Admin a new request came in, with a deep link."""
        admin_ids = list(
            User.objects.filter(role='csc_admin', is_active=True).values_list('id', flat=True)
        )
        if not admin_ids:
            logger.warning('Support request %s created but no active CSC Admin to notify.',
                           support_request.pk)
            return
        notify_users(
            admin_ids,
            Notification.Type.SUPPORT_REQUEST,
            title='New support request',
            message='{school} raised a "{issue}" request.'.format(
                school=support_request.school.name,
                issue=support_request.get_issue_type_display(),
            ),
            data={
                'support_request_id': support_request.pk,
                'school_code': support_request.school.code,
                'issue_type': support_request.issue_type,
                'link': '/admin/support-requests',
            },
        )

    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        """Super Admin replies to a request; the reply is delivered to the School Admin.

        By default this also resolves the request. The reply reaches the School Admin as an
        in-app notification — no email is sent.
        """
        support_request: SupportRequest = self.get_object()
        serializer = SupportRequestReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply_text = serializer.validated_data['reply']
        resolve = serializer.validated_data['resolve']

        with transaction.atomic():
            support_request.admin_reply = reply_text
            if resolve:
                support_request.status = SupportRequest.Status.RESOLVED
                support_request.resolved_by = request.user
                support_request.resolved_at = timezone.now()
            support_request.save()

            if support_request.raised_by_id:
                notify_user(
                    support_request.raised_by,
                    Notification.Type.SUPPORT_REQUEST,
                    title='Reply to your support request',
                    message=reply_text,
                    data={
                        'support_request_id': support_request.pk,
                        'issue_type': support_request.issue_type,
                        'status': support_request.status,
                        'link': '/notifications',
                    },
                )

        return Response(
            self.get_serializer(support_request).data,
            status=status.HTTP_200_OK,
        )
