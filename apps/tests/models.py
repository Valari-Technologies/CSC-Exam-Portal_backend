"""Test definition, question linking, and assignment to students."""
from django.conf import settings
from django.db import models


class Test(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED = 'archived', 'Archived'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='tests')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    subject = models.ForeignKey('academics.Subject', on_delete=models.PROTECT, related_name='tests')
    school_class = models.ForeignKey('academics.Class', on_delete=models.PROTECT, related_name='tests')

    total_marks = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    passing_marks = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    duration_minutes = models.PositiveIntegerField()
    instructions = models.TextField(blank=True)

    shuffle_questions = models.BooleanField(default=False)
    shuffle_options = models.BooleanField(default=False)
    show_result_immediately = models.BooleanField(default=False)
    allow_review_after_submit = models.BooleanField(default=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tests_test'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['school', 'status']),
            models.Index(fields=['subject', 'school_class']),
        ]

    def __str__(self):
        return self.title


class TestQuestion(models.Model):
    """Through model linking a Test to its Questions, preserving order."""

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='test_questions')
    question = models.ForeignKey('questions.Question', on_delete=models.PROTECT, related_name='test_questions')
    order_number = models.PositiveIntegerField()
    marks_override = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = 'tests_test_questions'
        ordering = ('test', 'order_number')
        unique_together = (('test', 'question'),)

    def __str__(self):
        return f'{self.test.title} - #{self.order_number}'


class TestAssignment(models.Model):
    """Defines when a test is assigned, to whom, and the schedule."""

    class AssignedToType(models.TextChoices):
        CLASS = 'class', 'Class'
        SECTION = 'section', 'Section'
        STUDENTS = 'students', 'Individual Students'

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='assignments')
    assigned_to_type = models.CharField(max_length=20, choices=AssignedToType.choices)
    school_class = models.ForeignKey(
        'academics.Class', on_delete=models.CASCADE, null=True, blank=True, related_name='+',
    )
    section = models.ForeignKey(
        'academics.Section', on_delete=models.CASCADE, null=True, blank=True, related_name='+',
    )

    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    # Legacy. Availability is controlled solely by start_datetime/end_datetime;
    # nothing reads this any more (removed from the assignment form, the write
    # and list serializers, and the start-exam checks). The column is kept
    # rather than dropped so existing rows are left untouched — re-introducing
    # an attempts limit would mean re-introducing a second availability control,
    # which is exactly what was removed.
    max_attempts = models.PositiveSmallIntegerField(default=1)

    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'tests_test_assignments'
        ordering = ('-start_datetime',)
        indexes = [
            models.Index(fields=['test', 'is_active']),
            models.Index(fields=['start_datetime', 'end_datetime']),
        ]

    def __str__(self):
        return f'Assignment: {self.test.title} ({self.assigned_to_type})'


class TestAssignmentStudent(models.Model):
    """Resolves a TestAssignment to specific Student users (used when type=students or for notification tracking)."""

    assignment = models.ForeignKey(TestAssignment, on_delete=models.CASCADE, related_name='student_assignments')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='test_assignments')
    is_notified = models.BooleanField(default=False)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tests_test_assignment_students'
        unique_together = (('assignment', 'student'),)
        indexes = [
            models.Index(fields=['student']),
        ]

    def __str__(self):
        return f'{self.assignment.test.title} -> {self.student.email}'
