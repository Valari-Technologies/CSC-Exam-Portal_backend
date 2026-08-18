"""School model — represents CSC-affiliated educational institutions."""
from django.conf import settings
from django.db import models


class School(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'
        SUSPENDED = 'suspended', 'Suspended'

    class Board(models.TextChoices):
        STATE_BOARD = 'state_board', 'State Board'
        CBSE = 'cbse', 'CBSE Board'
        MATRICULATION = 'matriculation', 'Matriculation'

    name = models.CharField(max_length=255)
    # The School ID, surfaced in the UI under that name. Server-generated on create as a
    # zero-padded sequence number (e.g. 001, 002…) and never edited after.
    code = models.CharField(max_length=20, unique=True, db_index=True,
                            help_text='School ID — auto-generated numeric sequence '
                                      '(e.g. 001). Not editable.')
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
    lan = models.CharField(max_length=20, blank=True, default='', help_text='School landline number (LAN).')
    logo = models.ImageField(upload_to='schools/logos/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    school_board = models.CharField(
        max_length=20, choices=Board.choices, default=Board.STATE_BOARD,
        help_text='The academic board the school is affiliated with.',
    )
    # The official government-issued school code, distinct from the internal School ID (code).
    school_code = models.CharField(
        max_length=50, blank=True, default='',
        help_text='Official government-issued school code.',
    )

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
