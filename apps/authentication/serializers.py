"""Serializers for authentication endpoints.

Conventions:
- All field names are snake_case to match DRF's default casing.
- Validation errors raise ValidationError with a clear, frontend-displayable message.
- Sensitive fields (password) are write_only.
"""
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken as SimpleJWTRefreshToken

from apps.schools.models import School
from .models import User
from .services import school_status_block_message


# ---------- User ----------

class UserSerializer(serializers.ModelSerializer):
    """Public-safe user representation. Used in /auth/me/ and embedded in login response."""

    school_name = serializers.CharField(source='school.name', read_only=True, default=None)
    # The human School ID (e.g. KAR_001), which is the `code` column — NOT the `school`
    # primary key above. Anything labelled "School ID" in the UI must read this; the PK
    # is an internal join key and means nothing to the person looking at it.
    school_code = serializers.CharField(source='school.code', read_only=True, default=None)
    # Absolute URL of the self-uploaded photo (built from the request when present), so the
    # frontend on a different origin can load it directly. `profile_photo` itself stays
    # writable — a multipart PATCH to /auth/me/ replaces it.
    profile_photo_url = serializers.SerializerMethodField()
    # The teacher's generated Teacher ID (e.g. KAR_TR_001), shown on the Teacher profile.
    # Null for every non-teacher — computed defensively so /auth/me never 500s for them.
    teacher_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'student_id',
            'teacher_id',
            'full_name',
            'role',
            'school',
            'school_name',
            'school_code',
            'is_active',
            'is_verified',
            'is_password_set',
            'must_change_password',
            'oauth_provider',
            'profile_picture',
            'profile_photo',
            'profile_photo_url',
            'last_login',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'email',
            'student_id',  # Assigned at creation; only an admin can change it, via /students/.
            'teacher_id',  # Server-generated; never client-editable.
            'full_name',  # Self-service name changes are disabled; admins edit names elsewhere.
            'role',
            'school',
            'school_name',
            'school_code',
            'is_active',
            'is_verified',
            'is_password_set',
            'must_change_password',
            'oauth_provider',
            'profile_photo_url',
            'last_login',
            'created_at',
            'updated_at',
        )

    def get_profile_photo_url(self, obj: User) -> str | None:
        if not obj.profile_photo:
            return None
        request = self.context.get('request')
        url = obj.profile_photo.url
        return request.build_absolute_uri(url) if request else url

    def get_teacher_id(self, obj: User) -> str | None:
        if obj.role != User.Role.TEACHER:
            return None
        # Reverse OneToOne access raises (not returns None) when there's no profile row,
        # so it must be caught rather than defaulted.
        try:
            return obj.teacher_profile.teacher_id
        except ObjectDoesNotExist:
            return None

    def validate_profile_photo(self, value):
        """Cap uploads at 5MB and restrict to JPG/JPEG/PNG — mirrors the frontend check."""
        if not value:
            return value
        max_bytes = 5 * 1024 * 1024
        if value.size > max_bytes:
            raise serializers.ValidationError('Photo must be 5MB or smaller.')
        allowed_exts = ('.jpg', '.jpeg', '.png')
        name = (getattr(value, 'name', '') or '').lower()
        if not name.endswith(allowed_exts):
            raise serializers.ValidationError('Photo must be a JPG, JPEG, or PNG image.')
        return value


# ---------- Register ----------

class RegisterSerializer(serializers.ModelSerializer):
    """Self-service registration.

    For MVP, only school_admin and student/teacher are not creatable here;
    students and teachers are provisioned by school_admin. csc_admin is provisioned
    via createsuperuser. So this endpoint is restricted to creating school_admin
    accounts that bind themselves to an existing school by code.
    """

    password = serializers.CharField(
        write_only=True,
        min_length=settings.PASSWORD_MIN_LENGTH,
        style={'input_type': 'password'},
    )
    school_code = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('email', 'full_name', 'password', 'school_code')

    def validate_email(self, value: str) -> str:
        value = value.lower().strip()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value

    def validate_school_code(self, value: str) -> str:
        if not School.objects.filter(code__iexact=value, status=School.Status.ACTIVE).exists():
            raise serializers.ValidationError('Invalid or inactive school code.')
        return value

    def validate_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data: dict) -> User:
        school_code = validated_data.pop('school_code')
        school = School.objects.get(code__iexact=school_code)
        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            full_name=validated_data['full_name'],
            role=User.Role.SCHOOL_ADMIN,
            school=school,
        )
        return user


# ---------- Login ----------

def enforce_school_status(user: User) -> None:
    """Reject a password login when the user's school is not Active.

    The rule itself lives in services.school_status_block_message so that every login door
    — email+password, student ID+password, and Google — enforces exactly one policy.
    """
    message = school_status_block_message(user)
    if message:
        raise serializers.ValidationError({'detail': message})


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    def validate(self, attrs: dict) -> dict:
        email = attrs.get('email', '').lower().strip()
        password = attrs.get('password', '')
        request = self.context.get('request')

        user = authenticate(request=request, username=email, password=password)
        if user is None:
            raise serializers.ValidationError({'detail': 'Invalid email or password.'})
        if not user.is_active:
            raise serializers.ValidationError({'detail': 'Account is deactivated.'})
        enforce_school_status(user)

        attrs['user'] = user
        return attrs


class StudentLoginSerializer(serializers.Serializer):
    """Student ID + password login — the only credential path for students.

    Deliberately does NOT go through django.contrib.auth.authenticate(): USERNAME_FIELD
    is still 'email', and changing it would ripple into the three email-based roles and
    the Django admin. Instead we resolve the User by student_id ourselves, then verify
    the password hash.
    """

    student_id = serializers.CharField()
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    def validate(self, attrs: dict) -> dict:
        student_id = attrs.get('student_id', '').strip()
        password = attrs.get('password', '')

        # One generic message for every failure mode below, so this endpoint can't be
        # used to enumerate which Student IDs exist.
        invalid = serializers.ValidationError({'detail': 'Invalid Student ID or password.'})

        user = User.objects.filter(
            student_id__iexact=student_id,
            role=User.Role.STUDENT,
        ).first()
        if user is None or not user.check_password(password):
            raise invalid
        if not user.is_active:
            raise serializers.ValidationError({'detail': 'Account is deactivated.'})
        enforce_school_status(user)

        attrs['user'] = user
        return attrs


# ---------- Logout ----------

class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate(self, attrs: dict) -> dict:
        try:
            token = SimpleJWTRefreshToken(attrs['refresh'])
            attrs['token_obj'] = token
        except Exception:
            raise serializers.ValidationError({'refresh': 'Invalid refresh token.'})
        return attrs


# ---------- Password reset ----------

class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.lower().strip()


class StudentPasswordResetRequestSerializer(serializers.Serializer):
    """Students don't know their login email — they identify themselves by Student ID.

    The reset link is then sent to that student's own registered email address. It is never
    sent to the school's official_email, which is a contact address, not a credential.
    """

    student_id = serializers.CharField()

    def validate_student_id(self, value: str) -> str:
        return value.strip()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(
        min_length=settings.PASSWORD_MIN_LENGTH,
        write_only=True,
        style={'input_type': 'password'},
    )

    def validate_new_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


# ---------- Change password (authenticated) ----------

class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    new_password = serializers.CharField(
        min_length=settings.PASSWORD_MIN_LENGTH,
        write_only=True,
        style={'input_type': 'password'},
    )

    def validate_old_password(self, value: str) -> str:
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Current password is incorrect.')
        return value

    def validate_new_password(self, value: str) -> str:
        try:
            validate_password(value, user=self.context['request'].user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


# ---------- Setup password (first login) ----------

class SetupPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(
        min_length=settings.PASSWORD_MIN_LENGTH,
        write_only=True,
        style={'input_type': 'password'},
    )

    def validate_new_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


# ---------- Google OAuth ----------

class GoogleLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField()
