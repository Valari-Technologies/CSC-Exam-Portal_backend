"""Support requests a School Admin raises to the CSC (Super) Admin.

This is the "Additional Details" workflow: a School Admin flags a correction their school
needs (wrong name, wrong login email, a password problem, or anything else), the Super
Admin reviews it and replies. The whole exchange is IN-APP only — a support request never
sends email and never touches School.official_email, which is a contact address and never
a credential.
"""
from django.conf import settings
from django.db import models


class SupportRequest(models.Model):
    class IssueType(models.TextChoices):
        INCORRECT_SCHOOL_NAME = 'incorrect_school_name', 'Incorrect School Name'
        INCORRECT_LOGIN_EMAIL = 'incorrect_login_email', 'Incorrect School Login Email'
        PASSWORD_ISSUE = 'password_issue', 'Password Issue'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        RESOLVED = 'resolved', 'Resolved'

    school = models.ForeignKey(
        'schools.School',
        on_delete=models.CASCADE,
        related_name='support_requests',
    )
    # The School Admin who raised it. SET_NULL so removing the account doesn't erase the
    # history the Super Admin may still be working through.
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='support_requests_raised',
    )
    issue_type = models.CharField(max_length=40, choices=IssueType.choices)
    description = models.TextField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True,
    )
    # The Super Admin's reply, delivered back to the School Admin as an in-app notification.
    admin_reply = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='support_requests_resolved',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'support_requests'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['school', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f'SupportRequest<{self.school_id}:{self.issue_type}:{self.status}>'
