"""Online exam session, answer tracking, and anti-cheating event logs."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ExamSession(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Not Started'
        STARTED = 'started', 'Started'
        IN_PROGRESS = 'in_progress', 'In Progress'
        SUBMITTED = 'submitted', 'Submitted'
        EVALUATED = 'evaluated', 'Evaluated'
        PUBLISHED = 'published', 'Published'
        ABANDONED = 'abandoned', 'Abandoned'

    # Lifecycle: started -> in_progress -> submitted -> evaluated -> published.
    # 'evaluated' may re-enter itself (teacher re-adjusts marks before publishing)
    # and 'published' may step back to 'evaluated' (unpublish toggle).
    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        Status.NOT_STARTED: {Status.STARTED},
        Status.STARTED: {Status.IN_PROGRESS, Status.ABANDONED},
        Status.IN_PROGRESS: {Status.SUBMITTED, Status.ABANDONED},
        Status.SUBMITTED: {Status.EVALUATED},
        Status.EVALUATED: {Status.EVALUATED, Status.PUBLISHED},
        Status.PUBLISHED: {Status.EVALUATED},  # unpublish
        Status.ABANDONED: set(),
    }

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='exam_sessions')
    assignment = models.ForeignKey('tests.TestAssignment', on_delete=models.CASCADE, related_name='sessions')
    test = models.ForeignKey('tests.Test', on_delete=models.CASCADE, related_name='sessions')

    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    time_remaining_seconds = models.IntegerField(
        help_text='Server-authoritative remaining time. Decremented periodically.'
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS, db_index=True)
    evaluated_at = models.DateTimeField(null=True, blank=True)
    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    browser_info = models.CharField(max_length=500, blank=True)

    def transition_to(self, new_status: str, extra_update_fields: list[str] | None = None) -> None:
        """Move to *new_status*, enforcing the lifecycle map. Saves the row."""
        allowed = self.ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValidationError(
                f'Invalid exam status transition: {self.status} -> {new_status}.'
            )
        self.status = new_status
        self.save(update_fields=['status'] + (extra_update_fields or []))

    class Meta:
        db_table = 'exam_sessions'
        indexes = [
            models.Index(fields=['student', 'status']),
            models.Index(fields=['assignment', 'student']),
        ]

    def __str__(self):
        return f'Session<{self.student.email} / {self.test.title}>'


class ExamAnswer(models.Model):
    """A student's answer to one question in a session. Created on first save, updated thereafter."""

    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey('questions.Question', on_delete=models.PROTECT, related_name='+')
    selected_option = models.CharField(
        max_length=1, null=True, blank=True, help_text='a/b/c/d, or null if unanswered',
    )
    time_spent_seconds = models.PositiveIntegerField(default=0)
    last_updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'exam_answers'
        unique_together = (('session', 'question'),)
        indexes = [
            models.Index(fields=['session']),
        ]


class AntiCheatLog(models.Model):
    class EventType(models.TextChoices):
        TAB_SWITCH = 'tab_switch', 'Tab Switch'
        FULLSCREEN_EXIT = 'fullscreen_exit', 'Fullscreen Exit'
        COPY_ATTEMPT = 'copy_attempt', 'Copy Attempt'
        FOCUS_LOSS = 'focus_loss', 'Focus Loss'

    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, related_name='cheat_events')
    event_type = models.CharField(max_length=30, choices=EventType.choices, db_index=True)
    event_count = models.PositiveSmallIntegerField(default=1)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'anti_cheat_logs'
        indexes = [
            models.Index(fields=['session', 'event_type']),
        ]

    def __str__(self):
        return f'{self.event_type} x{self.event_count} on session {self.session_id}'
