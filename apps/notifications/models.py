"""In-app notifications and per-user preferences."""
from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Type(models.TextChoices):
        TEST_ASSIGNED = 'test_assigned', 'Test Assigned'
        EXAM_COMPLETED = 'exam_completed', 'Exam Completed'
        RESULT_PUBLISHED = 'result_published', 'Result Published'
        SYSTEM_ALERT = 'system_alert', 'System Alert'
        REMINDER = 'reminder', 'Reminder'
        # Additional Details workflow: a School Admin's request to the Super Admin, and the
        # Super Admin's reply back. One type covers both directions — the recipient makes it
        # unambiguous.
        SUPPORT_REQUEST = 'support_request', 'Support Request'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    type = models.CharField(max_length=30, choices=Type.choices, db_index=True)
    title = models.CharField(max_length=255)
    message = models.TextField()
    data = models.JSONField(default=dict, blank=True,
                            help_text='Entity references, e.g. {"test_id": 5, "assignment_id": 12}')
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['user', 'is_read']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f'{self.type}: {self.title} ({self.user.email})'


class NotificationPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_pref')
    email_enabled = models.BooleanField(default=False)
    in_app_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notification_preferences'
