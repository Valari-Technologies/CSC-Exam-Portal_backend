"""Teacher profile + class/section assignments."""
from django.conf import settings
from django.db import models
from django.db.models import Q


class TeacherProfile(models.Model):
    class Gender(models.TextChoices):
        MALE = 'male', 'Male'
        FEMALE = 'female', 'Female'
        OTHER = 'other', 'Other'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='teacher_profile')
    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='teachers')
    # Server-generated on create from the school's School ID, e.g. KAR_001 -> KAR_TR_001.
    # Distinct from employee_id, which is the school's own free-text HR reference and stays
    # entirely under the admin's control.
    teacher_id = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Teacher ID — auto-generated from the School ID (e.g. KAR_TR_001).',
    )
    employee_id = models.CharField(max_length=50, blank=True)
    contact_number = models.CharField(max_length=20, blank=True, default='')
    # Blank is a real answer here, not a placeholder: teachers created before this field
    # existed have no value, and the form does not force one.
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True, default='')
    qualification = models.CharField(max_length=255, blank=True)
    joining_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'teacher_profiles'
        indexes = [
            models.Index(fields=['school', 'is_active']),
        ]
        constraints = [
            # Unique per school, exactly as specified — the sequence runs independently for
            # each school. Blanks are exempt so rows predating this field (or created by a
            # path that doesn't generate one) can't collide with each other.
            models.UniqueConstraint(
                fields=['school', 'teacher_id'],
                condition=~Q(teacher_id=''),
                name='unique_teacher_id_per_school',
            ),
        ]

    def __str__(self):
        return f'Teacher: {self.user.full_name}'


class TeacherAssignment(models.Model):
    """Assigns a teacher to a class (optionally narrowed to a section, optionally a subject).

    Two shapes of row live here:
    - class-only: what the Add Teacher form creates — the School Admin assigns the teacher
      to Class 8 / Section A without naming a subject yet. `subject` is null.
    - subject-bound: the older shape, where the assignment names the subject taught.

    `subject` is nullable so the first shape is possible at all; a school starts with no
    subjects, so requiring one would make assigning a teacher impossible on day one.
    """

    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='assignments')
    subject = models.ForeignKey(
        'academics.Subject',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='teacher_assignments',
        help_text='Optional — class/section assignments made at teacher creation have none.',
    )
    school_class = models.ForeignKey('academics.Class', on_delete=models.CASCADE, related_name='teacher_assignments')
    section = models.ForeignKey(
        'academics.Section',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='teacher_assignments',
        help_text='If null, assignment covers all sections of the class',
    )
    academic_year = models.CharField(max_length=20, blank=True, default='', help_text='e.g. "2025-26"')
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'teacher_assignments'
        # Covers subject-bound rows. Postgres treats NULLs as distinct, so this does NOT
        # constrain class-only rows — hence the partial constraint below.
        unique_together = (('teacher', 'subject', 'school_class', 'section', 'academic_year'),)
        indexes = [
            models.Index(fields=['teacher', 'academic_year']),
            models.Index(fields=['school_class', 'subject']),
        ]
        constraints = [
            # Without this, a null subject would let the same teacher be assigned the same
            # class/section over and over — unique_together can't see NULL duplicates.
            models.UniqueConstraint(
                fields=['teacher', 'school_class', 'section'],
                condition=Q(subject__isnull=True),
                name='unique_class_assignment_per_teacher',
            ),
            # NULL section is invisible to the constraint above for the same reason, so
            # whole-class (all-sections) rows need their own.
            models.UniqueConstraint(
                fields=['teacher', 'school_class'],
                condition=Q(subject__isnull=True, section__isnull=True),
                name='unique_whole_class_assignment_per_teacher',
            ),
        ]

    def __str__(self):
        return f'{self.teacher.user.full_name} -> {self.subject.name} / {self.school_class.name}'
