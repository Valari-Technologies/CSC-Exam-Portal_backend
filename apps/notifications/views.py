"""Notification list, mark-read, mark-all-read, unread count, delete + preference management.

Permission model:
- All authenticated users can access their own notifications and preferences.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification, NotificationPreference
from .serializers import NotificationPreferenceSerializer, NotificationSerializer

logger = logging.getLogger(__name__)


class NotificationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, delete + custom actions for user notifications.

    All queries are scoped to ``request.user`` — a user can never see, modify,
    or delete another user's notifications.  Notifications are created
    server-side via ``notify_user()`` — there is no client-facing create endpoint.
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user)

    def perform_destroy(self, instance: Notification) -> None:
        instance.delete()

    # -- Custom actions -------------------------------------------------------

    @action(detail=True, methods=['post', 'patch'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        """Mark a single notification as read."""
        notification: Notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=['is_read', 'read_at'])
        serializer = self.get_serializer(notification)
        return Response(serializer.data)

    @action(detail=False, methods=['post', 'patch'], url_path='mark-all-read')
    def mark_all_read(self, request):
        """Mark every unread notification for the current user as read."""
        now = timezone.now()
        updated = Notification.objects.filter(
            user=request.user,
            is_read=False,
        ).update(is_read=True, read_at=now)
        return Response({'updated': updated})

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        """Return the number of unread notifications."""
        count = Notification.objects.filter(
            user=request.user,
            is_read=False,
        ).count()
        return Response({'count': count})


class NotificationPreferenceView(APIView):
    """GET / PUT / PATCH the current user's notification preferences.

    If no preference record exists yet it is created with defaults on first GET.
    """

    permission_classes = [IsAuthenticated]

    def _get_or_create(self, user) -> NotificationPreference:
        pref, _ = NotificationPreference.objects.get_or_create(user=user)
        return pref

    def get(self, request):
        pref = self._get_or_create(request.user)
        serializer = NotificationPreferenceSerializer(pref)
        return Response(serializer.data)

    def put(self, request):
        pref = self._get_or_create(request.user)
        serializer = NotificationPreferenceSerializer(pref, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def patch(self, request):
        pref = self._get_or_create(request.user)
        serializer = NotificationPreferenceSerializer(pref, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
