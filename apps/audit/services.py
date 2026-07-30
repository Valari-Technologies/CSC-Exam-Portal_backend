"""Audit logging helper — call from anywhere to record an action."""
from typing import Optional

from django.contrib.auth import get_user_model
from django.http import HttpRequest

from .models import AuditLog


def log_action(
    user,
    action: str,
    *,
    entity_type: str = '',
    entity_id: Optional[int] = None,
    status: str = AuditLog.Status.SUCCESS,
    request: Optional[HttpRequest] = None,
    details: Optional[dict] = None,
) -> AuditLog:
    """Create an AuditLog entry. Safe to call without a request object."""
    ip_address = None
    user_agent = ''
    if request is not None:
        ip_address = (
            request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR')
        )
        user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

    User = get_user_model()
    return AuditLog.objects.create(
        user=user if isinstance(user, User) else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ip_address,
        user_agent=user_agent,
        status=status,
        details=details or {},
    )
