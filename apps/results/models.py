"""Exam results — auto-calculated on submission, with teacher-controlled visibility."""
from django.conf import settings
from django.db import models


class Result(models.Model):
    session = models.OneToOneField('exams.ExamSession', on_delete=models.CASCADE, related_name='result')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='results')
    test = models.ForeignKey('tests.Test', on_delete=models.CASCADE, related_name='results')
    assignment = models.ForeignKey('tests.TestAssignment', on_delete=models.CASCADE, related_name='results')

    total_marks = models.DecimalField(max_digits=7, decimal_places=2)
    obtained_marks = models.DecimalField(max_digits=7, decimal_places=2)
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    passed = models.BooleanField()

    correct_count = models.PositiveIntegerField(default=0)
    wrong_count = models.PositiveIntegerField(default=0)
    unattempted_count = models.PositiveIntegerField(default=0)

    rank = models.PositiveIntegerField(null=True, blank=True,
                                       help_text='Rank within the assignment (class/section scope)')
    time_taken_seconds = models.PositiveIntegerField()

    is_published = models.BooleanField(default=False, db_index=True,
                                       help_text='Teacher controls when students can see the result')
    published_at = models.DateTimeField(null=True, blank=True)
    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'results'
        indexes = [
            models.Index(fields=['student', 'is_published']),
            models.Index(fields=['test', 'rank']),
        ]
        constraints = [
            # Defense-in-depth: the scoring/evaluate code already clamps the total
            # to >= 0, but enforce it at the DB so a direct/raw write can never
            # persist a negative aggregate. (Per-question ResultDetail.marks_obtained
            # is intentionally NOT constrained — negative marking stores negatives.)
            models.CheckConstraint(
                check=models.Q(obtained_marks__gte=0),
                name='result_obtained_marks_non_negative',
            ),
            models.CheckConstraint(
                check=models.Q(total_marks__gte=0),
                name='result_total_marks_non_negative',
            ),
            models.CheckConstraint(
                check=models.Q(percentage__gte=0),
                name='result_percentage_non_negative',
            ),
        ]

    def __str__(self):
        return f'Result: {self.student.email} - {self.test.title} ({self.percentage}%)'


class ResultDetail(models.Model):
    """Per-question breakdown of a Result."""

    result = models.ForeignKey(Result, on_delete=models.CASCADE, related_name='details')
    question = models.ForeignKey('questions.Question', on_delete=models.PROTECT, related_name='+')
    selected_option = models.CharField(max_length=1, null=True, blank=True)
    correct_option = models.CharField(max_length=1)
    is_correct = models.BooleanField()
    marks_obtained = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        db_table = 'result_details'
        unique_together = (('result', 'question'),)
        indexes = [
            models.Index(fields=['result']),
        ]
