"""Serializers for the Additional Details / support-request workflow."""
from rest_framework import serializers

from .models import SupportRequest


class SupportRequestSerializer(serializers.ModelSerializer):
    """Read + create representation.

    The School Admin supplies only `issue_type` and `description`; everything that
    identifies the school (name, School ID, principal, official email) is derived
    server-side from the requester's own school, never trusted from the client.
    """

    issue_type_display = serializers.CharField(source='get_issue_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)
    # The human School ID (e.g. KAR_001) — the `code` column, never the school PK.
    school_code = serializers.CharField(source='school.code', read_only=True)
    school_official_email = serializers.EmailField(source='school.official_email', read_only=True)
    school_principal_name = serializers.CharField(source='school.principal_name', read_only=True)
    raised_by_name = serializers.CharField(
        source='raised_by.full_name', read_only=True, default=None,
    )
    raised_by_email = serializers.EmailField(
        source='raised_by.email', read_only=True, default=None,
    )
    resolved_by_name = serializers.CharField(
        source='resolved_by.full_name', read_only=True, default=None,
    )

    class Meta:
        model = SupportRequest
        fields = (
            'id',
            'school',
            'school_name',
            'school_code',
            'school_official_email',
            'school_principal_name',
            'raised_by',
            'raised_by_name',
            'raised_by_email',
            'issue_type',
            'issue_type_display',
            'description',
            'status',
            'status_display',
            'admin_reply',
            'resolved_by',
            'resolved_by_name',
            'resolved_at',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'school',
            'raised_by',
            'status',
            'admin_reply',
            'resolved_by',
            'resolved_at',
            'created_at',
            'updated_at',
        )

    def validate_description(self, value: str) -> str:
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Please describe the issue.')
        return value


class SupportRequestReplySerializer(serializers.Serializer):
    """The Super Admin's reply. `resolve` closes the request in the same step."""

    reply = serializers.CharField()
    resolve = serializers.BooleanField(required=False, default=True)

    def validate_reply(self, value: str) -> str:
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('A reply message is required.')
        return value
