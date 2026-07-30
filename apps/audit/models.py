"""Audit log — tracks login, exam activity, and sensitive admin actions."""
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    class Action(models.TextChoices):
        LOGIN = 'login', 'Login'
        LOGOUT = 'logout', 'Logout'
        LOGIN_FAILED = 'login_failed', 'Login Failed'
        EXAM_START = 'exam_start', 'Exam Start'
        EXAM_SUBMIT = 'exam_submit', 'Exam Submit'
        PASSWORD_CHANGE = 'password_change', 'Password Change'
        PASSWORD_RESET = 'password_reset', 'Password Reset'
        DATA_EXPORT = 'data_export', 'Data Export'
        USER_CREATED = 'user_created', 'User Created'
        USER_UPDATED = 'user_updated', 'User Updated'
        USER_DEACTIVATED = 'user_deactivated', 'User Deactivated'
        SCHOOL_CREATED = 'school_created', 'School Created'
        SCHOOL_UPDATED = 'school_updated', 'School Updated'
        RESULT_PUBLISHED = 'result_published', 'Result Published'
        BULK_IMPORT = 'bulk_import', 'Bulk Import'
        # Deletes the import *record*, not the students it created.
        BULK_IMPORT_DELETED = 'bulk_import_deleted', 'Import Record Deleted'
        TEST_PUBLISHED = 'test_published', 'Test Published'
        TEST_ASSIGNED = 'test_assigned', 'Test Assigned'
        EXAM_EVALUATED = 'exam_evaluated', 'Exam Evaluated'

    class Status(models.TextChoices):
        SUCCESS = 'success', 'Success'
        FAILURE = 'failure', 'Failure'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    entity_type = models.CharField(max_length=50, blank=True)
    entity_id = models.PositiveBigIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUCCESS)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['action', '-created_at']),
        ]

    def __str__(self):
        return f'{self.action} by {self.user_id} @ {self.created_at}'
