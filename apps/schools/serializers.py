"""Serializers for School CRUD + dashboard stats."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import School

User = get_user_model()


class SchoolSerializer(serializers.ModelSerializer):
    """Full School representation. Read-only metadata is server-managed.

    On CREATE this also provisions the school's first School Admin (see the admin_* fields
    below). Those fields are write-only and create-only: `official_email` belongs to the
    School and is purely a contact address, while the admin's login email lives on the User
    — the two are never the same field and never interchangeable.
    """

    logo_url = serializers.SerializerMethodField()

    # --- School Admin provisioning (create only) ---
    # The admin's login email + name are supplied on create; no password is chosen. The
    # admin sets their own password via a one-time setup link, surfaced (create-response
    # only) as `admin_setup_link` and emailed to them.
    admin_full_name = serializers.CharField(max_length=255, write_only=True, required=False)
    admin_email = serializers.EmailField(write_only=True, required=False)
    admin_setup_link = serializers.SerializerMethodField()

    class Meta:
        model = School
        fields = (
            'id',
            'name',
            'code',
            'address',
            'city',
            'state',
            'pincode',
            'principal_name',
            'official_email',
            'contact_phone',
            'logo',
            'logo_url',
            'status',
            'created_by',
            'created_at',
            'updated_at',
            'admin_full_name',
            'admin_email',
            'admin_setup_link',
        )
        # `code` is the School ID — server-generated from the school name on create and
        # immutable thereafter. It is the prefix of every student login ID issued by this
        # school, so letting it be edited would silently orphan those IDs.
        read_only_fields = ('id', 'code', 'created_by', 'created_at', 'updated_at', 'logo_url')

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get('request')
        url = obj.logo.url
        return request.build_absolute_uri(url) if request else url

    def get_admin_setup_link(self, obj) -> str | None:
        """The new admin's one-time password-setup link.

        Present only on the create response — the view stashes it on the instance right
        after provisioning. It is never persisted and never appears on reads/list/detail.
        """
        return getattr(obj, '_admin_setup_link', None)

    def validate_logo(self, value):
        """Restrict logos to PNG/JPG/JPEG and cap the size at 2MB."""
        if not value:
            return value
        max_bytes = 2 * 1024 * 1024
        if value.size > max_bytes:
            raise serializers.ValidationError('Logo must be 2MB or smaller.')
        allowed_exts = ('.png', '.jpg', '.jpeg')
        name = (getattr(value, 'name', '') or '').lower()
        if not name.endswith(allowed_exts):
            raise serializers.ValidationError('Logo must be a PNG, JPG, or JPEG image.')
        return value

    def validate_pincode(self, value: str) -> str:
        value = value.strip()
        if not value.isdigit() or not 4 <= len(value) <= 10:
            raise serializers.ValidationError('Pincode must be 4-10 digits.')
        return value

    def validate_admin_email(self, value: str) -> str:
        """The School Admin's LOGIN email — globally unique across all users and roles.

        Checked here so a duplicate returns a clean 400 instead of letting the User.email
        unique constraint surface as a 500 mid-transaction.
        """
        value = value.lower().strip()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this login email already exists.')
        return value

    def validate(self, attrs: dict) -> dict:
        """School Admin details are required on create, and rejected on update.

        Editing a school must never touch the admin's login email — that account is managed
        through its own endpoint (/schools/{id}/admins/).

        Note: item 4's "all fields required" is a FORM requirement (marked with *, blocked at
        submit), enforced in the frontend Add School schema. It is deliberately NOT mirrored
        here: `principal_name` stays optional at the API so existing provisioning flows and
        legacy schools with a blank principal keep working.
        """
        admin_fields = ('admin_full_name', 'admin_email')
        supplied = [f for f in admin_fields if attrs.get(f)]

        if self.instance is not None:
            if supplied:
                raise serializers.ValidationError({
                    'admin_email': 'School Admin details cannot be changed here. '
                                   'Use the School Admin account section instead.',
                })
            return attrs

        missing = [f for f in admin_fields if not attrs.get(f)]
        if missing:
            raise serializers.ValidationError({
                field: 'This field is required when creating a school.' for field in missing
            })
        return attrs


class SchoolListSerializer(serializers.ModelSerializer):
    """Compact representation for table views."""

    user_count = serializers.IntegerField(read_only=True)
    teachers_count = serializers.IntegerField(read_only=True)
    students_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = School
        fields = (
            'id',
            'name',
            'code',
            'city',
            'state',
            'official_email',
            'status',
            'user_count',
            'teachers_count',
            'students_count',
            'created_at',
        )


class SchoolAdminSerializer(serializers.ModelSerializer):
    """A school's School Admin account, as returned by /schools/{id}/admins/."""

    class Meta:
        model = User
        fields = (
            'id', 'email', 'full_name', 'is_active',
            'is_password_set', 'must_change_password', 'last_login',
        )
        read_only_fields = fields


class SchoolAdminWriteSerializer(serializers.Serializer):
    """Edit a School Admin's account — name, LOGIN email, or password.

    Separate from SchoolSerializer on purpose: editing a school's contact details must never
    be able to change who can log in. Every field is optional; only what is supplied changes.
    """

    full_name = serializers.CharField(max_length=255, required=False)
    email = serializers.EmailField(required=False)
    password = serializers.CharField(
        required=False, write_only=True, style={'input_type': 'password'},
    )
    is_active = serializers.BooleanField(required=False)

    def validate_email(self, value: str) -> str:
        value = value.lower().strip()
        qs = User.objects.filter(email__iexact=value)
        instance = self.context.get('instance')
        if instance is not None:
            qs = qs.exclude(pk=instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A user with this login email already exists.')
        return value

    def validate_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def update(self, instance: User, validated_data: dict) -> User:
        if 'full_name' in validated_data:
            instance.full_name = validated_data['full_name']
        if 'email' in validated_data:
            instance.email = validated_data['email']
        if 'is_active' in validated_data:
            instance.is_active = validated_data['is_active']
        if validated_data.get('password'):
            # A CSC-Admin-chosen password is temporary by definition.
            instance.set_password(validated_data['password'])
            instance.is_password_set = True
            instance.must_change_password = True
        instance.save()
        return instance


class SchoolStatsSerializer(serializers.Serializer):
    """Per-school aggregate counts for the detail page."""

    school_admins = serializers.IntegerField()
    teachers = serializers.IntegerField()
    students = serializers.IntegerField()
    classes = serializers.IntegerField()
    subjects = serializers.IntegerField()
    tests = serializers.IntegerField()


class PlatformStatsSerializer(serializers.Serializer):
    """Platform-wide counts for the CSC Admin dashboard."""

    total_schools = serializers.IntegerField()
    active_schools = serializers.IntegerField()
    total_users = serializers.IntegerField()
    total_school_admins = serializers.IntegerField()
    total_teachers = serializers.IntegerField()
    total_students = serializers.IntegerField()
    total_tests = serializers.IntegerField()
    exams_today = serializers.IntegerField()
