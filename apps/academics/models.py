"""Academic structure: Class, Section, Subject, Chapter."""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

# The platform supports Grades 1-10 only. Enforced three ways: field validators
# (DRF/forms), a DB check constraint (`classes`), and the seeding range in services.py.
MIN_GRADE = 1
MAX_GRADE = 10


class Class(models.Model):
    """A school's class/grade (e.g. 'Class 1').

    `class` is a Python reserved word, so the model is named `Class` but FKs use `school_class`.
    """

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='classes')
    name = models.CharField(max_length=50, help_text='e.g. "Class 1", "Grade X"')
    numeric_value = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(MIN_GRADE), MaxValueValidator(MAX_GRADE)],
        help_text=f'{MIN_GRADE}-{MAX_GRADE} for primary/secondary',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'classes'
        ordering = ('numeric_value', 'name')
        unique_together = (('school', 'name'),)
        indexes = [
            models.Index(fields=['school', 'is_active']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(numeric_value__gte=MIN_GRADE, numeric_value__lte=MAX_GRADE),
                name='class_numeric_value_within_supported_grades',
            ),
        ]
        verbose_name_plural = 'Classes'

    def __str__(self):
        return f'{self.name} ({self.school.code})'


class Section(models.Model):
    """A section within a class (e.g. 'A', 'B')."""

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='sections')
    school_class = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='sections')
    name = models.CharField(max_length=10, help_text='e.g. "A", "B"')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sections'
        ordering = ('school_class__numeric_value', 'name')
        unique_together = (('school_class', 'name'),)

    def __str__(self):
        return f'{self.school_class.name} - {self.name}'


class Subject(models.Model):
    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='subjects')
    school_class = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='subjects')
    name = models.CharField(max_length=100)
    # Subject ID — server-generated on create as <school 2-letter prefix>_<subject 3-letter
    # abbreviation>_<2-digit class>, e.g. KA_MAT_10. Not entered by users; kept in sync when
    # a subject's name or class changes. Unique within the school (constraint below).
    code = models.CharField(
        max_length=20, blank=True,
        help_text='Subject ID — auto-generated (e.g. KA_MAT_10). Not editable.',
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'subjects'
        ordering = ('name',)
        unique_together = (('school_class', 'name'),)
        indexes = [
            models.Index(fields=['school', 'is_active']),
        ]
        constraints = [
            # The Subject ID is unique within a school. Blanks are exempt so any legacy
            # row without a generated code can't collide (all current rows are backfilled).
            models.UniqueConstraint(
                fields=['school', 'code'],
                condition=~Q(code=''),
                name='unique_subject_code_per_school',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.school_class.name})'


class Chapter(models.Model):
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='chapters')
    name = models.CharField(max_length=200)
    order_number = models.PositiveSmallIntegerField(default=1)
    description = models.TextField(blank=True)
    lessons = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chapters'
        ordering = ('subject', 'order_number')
        unique_together = (('subject', 'name'),)

    def __str__(self):
        return f'{self.subject.name} - Ch.{self.order_number}: {self.name}'
