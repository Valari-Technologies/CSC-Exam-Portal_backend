"""Helpers to create notifications programmatically (single + bulk)."""
from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth import get_user_model

from .models import Notification

User = get_user_model()
logger = logging.getLogger(__name__)


def notify_user(
    user: Any,
    notification_type: str,
    title: str,
    message: str,
    data: dict | None = None,
) -> Notification:
    """Create an in-app notification for *user*.

    Parameters
    ----------
    user:
        The recipient ``User`` instance.
    notification_type:
        One of ``Notification.Type`` values (e.g. ``'test_assigned'``).
    title:
        Short headline shown in the notification list.
    message:
        Longer body text.
    data:
        Optional JSON-serialisable dict with entity references
        (e.g. ``{"test_id": 5, "assignment_id": 12}``).

    Returns
    -------
    Notification
        The newly created notification instance.
    """
    notification = Notification.objects.create(
        user=user,
        type=notification_type,
        title=title,
        message=message,
        data=data or {},
    )
    logger.info('Notification created: type=%s user=%s id=%s', notification_type, user.pk, notification.pk)
    return notification


def notify_users(
    user_ids: list[int],
    notification_type: str,
    title: str,
    message: str,
    data: dict | None = None,
) -> int:
    """Create the same in-app notification for many users at once.

    Uses ``bulk_create`` (one notification row per user) so notifying a
    1000+-student class is a single batched insert, not N queries.

    Returns the number of notifications created.
    """
    if not user_ids:
        return 0
    payload = data or {}
    notifications = [
        Notification(
            user_id=uid,
            type=notification_type,
            title=title,
            message=message,
            data=payload,
        )
        for uid in user_ids
    ]
    created = Notification.objects.bulk_create(notifications, batch_size=500)
    logger.info('Bulk notifications created: type=%s count=%s', notification_type, len(created))
    return len(created)
