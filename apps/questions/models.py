"""MCQ Question Bank — single-answer multiple choice with optional images."""
from django.conf import settings
from django.db import models


class Question(models.Model):
    class Difficulty(models.TextChoices):
        EASY = 'easy', 'Easy'
        MEDIUM = 'medium', 'Medium'
        HARD = 'hard', 'Hard'

    class CorrectOption(models.TextChoices):
        A = 'a', 'A'
        B = 'b', 'B'
        C = 'c', 'C'
        D = 'd', 'D'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='questions')
    subject = models.ForeignKey('academics.Subject', on_delete=models.CASCADE, related_name='questions')
    chapter = models.ForeignKey('academics.Chapter', on_delete=models.CASCADE, related_name='questions')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    question_text = models.TextField()
    question_image = models.ImageField(upload_to='questions/', null=True, blank=True)

    option_a = models.CharField(max_length=500)
    option_b = models.CharField(max_length=500)
    option_c = models.CharField(max_length=500)
    option_d = models.CharField(max_length=500)
    option_a_image = models.ImageField(upload_to='questions/options/', null=True, blank=True)
    option_b_image = models.ImageField(upload_to='questions/options/', null=True, blank=True)
    option_c_image = models.ImageField(upload_to='questions/options/', null=True, blank=True)
    option_d_image = models.ImageField(upload_to='questions/options/', null=True, blank=True)

    correct_option = models.CharField(max_length=1, choices=CorrectOption.choices)
    explanation = models.TextField(blank=True)

    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM, db_index=True)
    marks = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    negative_marks = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'questions'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['school', 'subject', 'difficulty']),
            models.Index(fields=['chapter', 'difficulty']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f'Q{self.id}: {self.question_text[:60]}...'
