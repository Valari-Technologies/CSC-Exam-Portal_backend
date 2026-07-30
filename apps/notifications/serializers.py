"""Serializers for notification and notification-preference models."""
from rest_framework import serializers

from .models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            'id',
            'user',
            'type',
            'title',
            'message',
            'data',
            'is_read',
            'created_at',
            'read_at',
        )
        read_only_fields = (
            'id',
            'user',
            'type',
            'title',
            'message',
            'data',
            'is_read',
            'created_at',
            'read_at',
        )


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = (
            'id',
            'user',
            'email_enabled',
            'in_app_enabled',
            'created_at',
        )
        read_only_fields = ('id', 'user', 'created_at')
