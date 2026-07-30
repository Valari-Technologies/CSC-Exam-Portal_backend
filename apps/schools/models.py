"""School model — represents CSC-affiliated educational institutions."""
from django.conf import settings
from django.db import models


class School(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'
        SUSPENDED = 'suspended', 'Suspended'

    name = models.CharField(max_length=255)
    # The School ID, surfaced in the UI under that name. Server-generated on create as
    # <first 3 letters of name>_<3-digit sequence> (e.g. GRE_001) and never edited after:
    # it is the prefix of every student login ID this school issues, so changing it would
    # orphan those IDs. Rows created before this scheme keep their original codes.
    code = models.CharField(max_length=20, unique=True, db_index=True,
                            help_text='School ID — auto-generated from the school name '
                                      '(e.g. GRE_001). Not editable.')
    address = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=10)
    principal_name = models.CharField(max_length=255, blank=True)
    # The school's official CONTACT address only. It is never a credential: authentication
    # is always User.email (staff) or User.student_id (students), and password-reset mail
    # goes to the user's own login email — never here.
    official_email = models.EmailField(
        help_text='Official contact email for the school. Not used for login or password reset.',
    )
    contact_phone = models.CharField(max_length=20)
    logo = models.ImageField(upload_to='schools/logos/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='schools_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'schools'
        ordering = ('name',)
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['code']),
        ]

    def __str__(self):
        return f'{self.name} ({self.code})'
