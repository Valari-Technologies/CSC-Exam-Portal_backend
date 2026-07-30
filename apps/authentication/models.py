"""Authentication models: custom User + RefreshToken."""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        # Blank email must become NULL, never ''. Students may have no email at all, and
        # Postgres allows unlimited NULLs under a unique index but exactly one '' — so two
        # email-less students would collide on the second one.
        email = self.normalize_email(email) if email else None
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        # Still required here: a superuser signs in with USERNAME_FIELD, so an email-less
        # one could never log in.
        if not email:
            raise ValueError('Email is required')
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('role', User.Role.CSC_ADMIN)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model — uses email as USERNAME_FIELD and has a role-based RBAC."""

    class Role(models.TextChoices):
        CSC_ADMIN = 'csc_admin', 'CSC Admin'
        SCHOOL_ADMIN = 'school_admin', 'School Admin'
        TEACHER = 'teacher', 'Teacher'
        STUDENT = 'student', 'Student'

    # NULL-able because a student may not have an email address: they sign in with a
    # Student ID, so an email is contact information for them rather than a credential.
    # Every other role still authenticates with it (USERNAME_FIELD), and the serializers
    # keep it required for those. Unique still holds — Postgres does not compare NULLs,
    # so any number of email-less students coexist. Blank MUST be stored as NULL, not ''.
    email = models.EmailField(unique=True, db_index=True, null=True, blank=True)
    full_name = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=Role.choices, db_index=True)
    # Login identifier for students only (they sign in at /studentlogin with this
    # instead of an email). NULL for every other role — Postgres allows unlimited
    # NULLs under a unique constraint, so non-students never collide. Globally
    # unique because the student login form has no school selector; the school
    # code prefix (e.g. KNS-0001) makes that unique-across-schools for free.
    student_id = models.CharField(
        max_length=30,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text='Student login ID (students only; null for other roles)',
    )
    school = models.ForeignKey(
        'schools.School',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='users',
        help_text='Null for CSC Admin; required for other roles',
    )
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    is_password_set = models.BooleanField(
        default=True,
        help_text='False when account is provisioned by admin without a password',
    )
    # Distinct from is_password_set: a school admin provisioned with a TEMPORARY password
    # has is_password_set=True (a usable password exists) but must_change_password=True
    # (they must replace it on first login). default=False leaves every existing user
    # untouched.
    must_change_password = models.BooleanField(
        default=False,
        help_text='True when the account was given a temporary password that must be changed at next login',
    )

    # OAuth
    oauth_provider = models.CharField(max_length=20, null=True, blank=True)
    oauth_id = models.CharField(max_length=255, null=True, blank=True)
    # An externally hosted picture URL — set only by Google OAuth. Kept distinct from
    # profile_photo below so a self-uploaded photo and an OAuth avatar never overwrite
    # each other.
    profile_picture = models.URLField(null=True, blank=True)
    # A photo the user uploads themselves (Profile page). JPG/PNG, capped at 5MB — both
    # enforced in the serializer. Nullable so every existing user is untouched.
    profile_photo = models.ImageField(upload_to='users/photos/', null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_login = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name', 'role']

    class Meta:
        db_table = 'users'
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['role']),
            models.Index(fields=['school', 'role']),
        ]

    def __str__(self):
        return f'{self.email} ({self.role})'

    @property
    def is_csc_admin(self):
        return self.role == self.Role.CSC_ADMIN

    @property
    def is_school_admin(self):
        return self.role == self.Role.SCHOOL_ADMIN

    @property
    def is_teacher(self):
        return self.role == self.Role.TEACHER

    @property
    def is_student(self):
        return self.role == self.Role.STUDENT


class RefreshToken(models.Model):
    """Refresh token tracking for audit + revocation."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='refresh_tokens')
    token = models.CharField(max_length=500, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'refresh_tokens'
        indexes = [
            models.Index(fields=['user', 'revoked']),
        ]

    def __str__(self):
        return f'RefreshToken<{self.user.email}>'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at
