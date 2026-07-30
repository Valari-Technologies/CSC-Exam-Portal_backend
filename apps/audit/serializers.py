"""Serializers for audit log entries."""
from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True, default=None)
    user_name = serializers.CharField(source='user.full_name', read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = (
            'id',
            'user',
            'user_email',
            'user_name',
            'action',
            'entity_type',
            'entity_id',
            'ip_address',
            'status',
            'details',
            'created_at',
        )
        read_only_fields = fields
