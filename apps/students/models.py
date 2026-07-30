"""Student profile + bulk CSV import logging."""
from django.conf import settings
from django.db import models


class StudentProfile(models.Model):
    class Gender(models.TextChoices):
        MALE = 'male', 'Male'
        FEMALE = 'female', 'Female'
        OTHER = 'other', 'Other'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='student_profile')
    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='students')
    school_class = models.ForeignKey('academics.Class', on_delete=models.PROTECT, related_name='students')
    section = models.ForeignKey('academics.Section', on_delete=models.PROTECT, related_name='students')
    roll_number = models.CharField(max_length=20)
    admission_number = models.CharField(max_length=50, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    parent_name = models.CharField(max_length=255, blank=True)
    parent_phone = models.CharField(max_length=20, blank=True)
    sub_section_code = models.CharField(
        max_length=1,
        choices=[('a', 'a'), ('b', 'b'), ('c', 'c')],
        null=True,
        blank=True,
        help_text='Optional sub-section (a/b/c) for sections A, B, or C only',
    )
    is_active = models.BooleanField(default=True)
    enrollment_date = models.DateField(auto_now_add=True)

    class Meta:
        db_table = 'student_profiles'
        unique_together = (('school_class', 'section', 'roll_number'),)
        indexes = [
            models.Index(fields=['school', 'is_active']),
            models.Index(fields=['school_class', 'section']),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.sub_section_code and self.section and self.section.name.upper() not in ('A', 'B', 'C'):
            raise ValidationError({'sub_section_code': 'Sub-section is only allowed for sections A, B, or C.'})

    @property
    def display_section(self) -> str:
        section_name = self.section.name if self.section else ''
        if self.sub_section_code:
            return f'{section_name}({self.sub_section_code})'
        return section_name

    @property
    def display_class_section(self) -> str:
        """Combined class+section label, e.g. '11-A' or '11-A(a)'."""
        class_value = self.school_class.numeric_value if self.school_class else ''
        return f'{class_value}-{self.display_section}'

    def __str__(self):
        return f'Student: {self.user.full_name} ({self.roll_number})'


class BulkImportLog(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='bulk_imports')
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    file_name = models.CharField(max_length=255)
    total_rows = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    fail_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True,
                              help_text='List of {row, error} objects for failed rows')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bulk_import_logs'
        ordering = ('-created_at',)

    def __str__(self):
        return f'Import {self.id} - {self.status} ({self.success_count}/{self.total_rows})'
