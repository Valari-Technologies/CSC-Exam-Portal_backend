"""Serializers for student profiles and bulk import logs."""
from __future__ import annotations

import logging
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.academics.models import Class, Section
from apps.schools.models import School

from .file_parsers import SUPPORTED_EXTENSIONS, parse_upload_file
from .import_template import ALL_COLUMNS, REQUIRED_COLUMNS
from .models import BulkImportLog, StudentProfile
from .services import generate_student_id, generate_student_password

User = get_user_model()
logger = logging.getLogger(__name__)


class StudentUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'student_id', 'full_name', 'is_active', 'last_login', 'created_at')
        read_only_fields = fields


class StudentProfileListSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True, allow_null=True)
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    student_id = serializers.CharField(source='user.student_id', read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)
    # The human School ID (e.g. KAR_001) — the `code` column, never the school PK.
    school_code = serializers.CharField(source='school.code', read_only=True)
    class_name = serializers.CharField(source='school_class.name', read_only=True)
    section_name = serializers.CharField(source='section.name', read_only=True)

    display_section = serializers.CharField(read_only=True)
    display_class_section = serializers.CharField(read_only=True)

    class Meta:
        model = StudentProfile
        fields = (
            'id',
            'user',
            'user_email',
            'user_name',
            'student_id',
            'school',
            'school_name',
            'school_code',
            'school_class',
            'class_name',
            'section',
            'section_name',
            'sub_section_code',
            'display_section',
            'display_class_section',
            'roll_number',
            'admission_number',
            'gender',
            'is_active',
            'enrollment_date',
        )
        read_only_fields = fields


class StudentProfileDetailSerializer(serializers.ModelSerializer):
    user = StudentUserSerializer(read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)
    # The human School ID (e.g. KAR_001) — the `code` column, never the school PK.
    school_code = serializers.CharField(source='school.code', read_only=True)
    class_name = serializers.CharField(source='school_class.name', read_only=True)
    section_name = serializers.CharField(source='section.name', read_only=True)
    display_section = serializers.CharField(read_only=True)
    display_class_section = serializers.CharField(read_only=True)
    student_id = serializers.CharField(source='user.student_id', read_only=True)
    initial_password = serializers.SerializerMethodField()

    def get_initial_password(self, obj) -> str | None:
        """The plaintext password, present ONLY in the response to the request that just
        created it (or reset it). Read from a transient attribute the write serializer
        sets — a plain GET always returns null, because it is never persisted.
        """
        return getattr(obj, 'initial_password', None)

    class Meta:
        model = StudentProfile
        fields = (
            'id',
            'user',
            'student_id',
            'initial_password',
            'school',
            'school_name',
            'school_code',
            'school_class',
            'class_name',
            'section',
            'section_name',
            'sub_section_code',
            'display_section',
            'display_class_section',
            'roll_number',
            'admission_number',
            'date_of_birth',
            'gender',
            'parent_name',
            'parent_phone',
            'is_active',
            'enrollment_date',
        )
        read_only_fields = fields


class StudentProfileWriteSerializer(serializers.Serializer):
    """Creates a User + StudentProfile in one request.

    Students log in with a Student ID + password (not email), so both are established
    here at creation time. Either may be left blank and will be generated; the
    resulting credentials are surfaced once in the create response for handout.
    """
    # Optional: a student signs in with their Student ID, so an email is contact
    # information rather than a credential, and plenty of students have none.
    email = serializers.EmailField(
        required=False, allow_blank=True, allow_null=True, default=None,
    )
    full_name = serializers.CharField(max_length=255)
    student_id = serializers.CharField(
        max_length=30, required=False, allow_blank=True, default='',
        help_text='Leave blank to auto-generate from the school code (e.g. KNS-0001).',
    )
    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, default='',
        style={'input_type': 'password'},
        help_text='Leave blank to auto-generate. Returned once in the create response.',
    )
    school = serializers.PrimaryKeyRelatedField(queryset=School.objects.all(), required=False)
    school_class = serializers.PrimaryKeyRelatedField(queryset=Class.objects.all())
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all())
    roll_number = serializers.CharField(max_length=20)
    admission_number = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    date_of_birth = serializers.DateField(required=False, allow_null=True, default=None)
    gender = serializers.ChoiceField(
        choices=StudentProfile.Gender.choices,
        required=False,
        allow_blank=True,
        default='',
    )
    parent_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    parent_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    sub_section_code = serializers.ChoiceField(
        choices=[('a', 'a'), ('b', 'b'), ('c', 'c')],
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
    )
    is_active = serializers.BooleanField(default=True)

    def validate_email(self, value: str | None) -> str | None:
        # Blank must become None, not '': the column is unique, and Postgres permits any
        # number of NULLs but only ever one ''. Two email-less students would otherwise
        # collide on the second one.
        value = (value or '').lower().strip()
        if not value:
            return None

        instance = self.context.get('instance')
        qs = User.objects.filter(email__iexact=value)
        if instance:
            qs = qs.exclude(pk=instance.user_id)
        if qs.exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_student_id(self, value: str) -> str:
        value = (value or '').strip().upper()
        if not value:
            return ''  # Blank means "generate one" — handled in create().

        instance = self.context.get('instance')
        qs = User.objects.filter(student_id__iexact=value)
        if instance:
            qs = qs.exclude(pk=instance.user_id)
        if qs.exists():
            raise serializers.ValidationError('This Student ID is already taken.')
        return value

    def validate_password(self, value: str) -> str:
        if not value:
            return ''  # Blank means "generate one" (create) or "leave unchanged" (update).
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    @staticmethod
    def _require_fields_on_create(attrs: dict) -> None:
        """Item 3: Add Student requires every field EXCEPT Email (kept optional — students
        sign in with a Student ID). Login credentials (student_id/password) stay optional
        because they auto-generate, and sub_section_code is conditional (only sections A/B/C
        have one). Called only on CREATE, so editing a legacy student with a blank field is
        never blocked."""
        required_fields = (
            'admission_number', 'date_of_birth', 'gender', 'parent_name', 'parent_phone',
        )
        missing = {
            field: 'This field is required.'
            for field in required_fields
            if attrs.get(field) in (None, '')
        }
        if missing:
            raise serializers.ValidationError(missing)

    def validate(self, attrs: dict) -> dict:
        request = self.context.get('request')
        instance = self.context.get('instance')
        school = attrs.get('school')

        # `instance` (from context) is the create/update signal for this plain Serializer.
        if instance is None:
            self._require_fields_on_create(attrs)

        if school is None:
            if instance:
                school = instance.school
                attrs['school'] = school
            elif request and request.user.role == 'csc_admin':
                raise serializers.ValidationError({'school': 'CSC Admin must specify a school.'})
            elif request:
                school = request.user.school
                attrs['school'] = school

        school_class: Class | None = attrs.get('school_class') or (instance.school_class if instance else None)
        section: Section | None = attrs.get('section') or (instance.section if instance else None)
        roll_number: str | None = attrs.get('roll_number') or (instance.roll_number if instance else None)

        if school and school_class and school_class.school_id != school.pk:
            raise serializers.ValidationError({'school_class': 'Class must belong to the selected school.'})
        if school_class and section and section.school_class_id != school_class.pk:
            raise serializers.ValidationError({'section': 'Section must belong to the selected class.'})

        if school_class and section and roll_number:
            dup_qs = StudentProfile.objects.filter(
                school_class=school_class,
                section=section,
                roll_number=roll_number,
            )
            if instance:
                dup_qs = dup_qs.exclude(pk=instance.pk)
            if dup_qs.exists():
                raise serializers.ValidationError(
                    {'roll_number': 'This roll number is already taken in this class/section.'}
                )

        self._normalize_sub_section(attrs, section)
        return attrs

    @staticmethod
    def _normalize_sub_section(attrs: dict, section: 'Section | None') -> None:
        """A sub-section is only valid for sections A/B/C; anything else is cleared to None."""
        sub_section_code = attrs.get('sub_section_code')
        if sub_section_code and sub_section_code.strip():
            if section and section.name.upper() not in ('A', 'B', 'C'):
                raise serializers.ValidationError(
                    {'sub_section_code': 'Sub-section is only allowed for sections A, B, or C.'},
                )
        else:
            attrs['sub_section_code'] = None

    @transaction.atomic
    def create(self, validated_data: dict) -> StudentProfile:
        school = validated_data.pop('school')

        student_id = validated_data.pop('student_id', '') or generate_student_id(school)
        password = validated_data.pop('password', '') or generate_student_password()

        user = User.objects.create_user(
            email=validated_data.pop('email'),
            password=password,
            full_name=validated_data.pop('full_name'),
            role=User.Role.STUDENT,
            school=school,
            student_id=student_id,
            # The student has a real password from this moment on — no emailed setup
            # step (students may not have a usable inbox). The admin hands them the
            # credentials returned below.
            is_password_set=True,
        )
        profile = StudentProfile.objects.create(user=user, school=school, **validated_data)

        # Surfaced once, by the view, so the admin can print/copy it. Never stored.
        profile.initial_password = password
        return profile

    @transaction.atomic
    def update(self, instance: StudentProfile, validated_data: dict) -> StudentProfile:
        user = instance.user
        # Only touch the email if the caller actually sent one. `email` carries
        # default=None (blank means "no email" on create), and DRF fills defaults for
        # missing fields on a full update — so keying off validated_data alone would let
        # a PUT that never mentions email silently erase the address of a student who
        # has one. Sending an explicit null or '' still clears it, deliberately.
        email_was_sent = 'email' in getattr(self, 'initial_data', {})
        new_email = validated_data.pop('email', None)
        if email_was_sent:
            user.email = new_email
        if 'full_name' in validated_data:
            user.full_name = validated_data.pop('full_name')
        if 'is_active' in validated_data:
            user.is_active = validated_data['is_active']

        # Blank on update means "leave as-is", so an admin editing a student's class
        # doesn't wipe their Student ID or reset their password by omission.
        # Track whether the LOGIN CREDENTIALS actually change — an unchanged Student ID
        # resubmitted, or an ordinary edit, must NOT log the student out (item 6).
        credentials_changed = False

        student_id = validated_data.pop('student_id', '')
        if student_id and student_id != user.student_id:
            user.student_id = student_id
            credentials_changed = True

        new_password = validated_data.pop('password', '')
        if new_password:
            user.set_password(new_password)
            user.is_password_set = True
            instance.initial_password = new_password
            credentials_changed = True

        user.save()

        # Changing the Student ID or password invalidates the student's existing sessions:
        # blacklist their outstanding refresh tokens so they must sign in again with the new
        # credentials. (Their current access token remains valid until it expires — up to the
        # 30-minute access-token lifetime — after which refresh fails and they are logged out.)
        if credentials_changed:
            from apps.authentication.services import revoke_all_user_refresh_tokens
            revoke_all_user_refresh_tokens(user)

        validated_data.pop('school', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class BulkImportLogSerializer(serializers.ModelSerializer):
    credentials = serializers.SerializerMethodField()

    def get_credentials(self, obj) -> list | None:
        """Login credentials for the students this upload just created.

        Present ONLY in the response to the upload itself — read from a transient
        attribute the upload serializer sets. Listing import history always returns null
        here, because the plaintext passwords are never stored anywhere.
        """
        return getattr(obj, 'credentials', None)

    class Meta:
        model = BulkImportLog
        fields = (
            'id',
            'school',
            'imported_by',
            'file_name',
            'total_rows',
            'success_count',
            'fail_count',
            'errors',
            'credentials',
            'status',
            'created_at',
        )
        read_only_fields = fields


# What people actually type in a gender column. Anything outside this is rejected rather
# than stored: the column has choices, but Django does not enforce choices on save(), so an
# unrecognised value used to land in the database and show up as a broken filter later.
_GENDER_ALIASES = {
    'm': StudentProfile.Gender.MALE,
    'male': StudentProfile.Gender.MALE,
    'boy': StudentProfile.Gender.MALE,
    'f': StudentProfile.Gender.FEMALE,
    'female': StudentProfile.Gender.FEMALE,
    'girl': StudentProfile.Gender.FEMALE,
    'o': StudentProfile.Gender.OTHER,
    'other': StudentProfile.Gender.OTHER,
}


def normalize_gender(value: str) -> str:
    """Map a free-text gender cell onto a Gender choice. Blank stays blank."""
    text = (value or '').strip().lower()
    if not text:
        return ''
    try:
        return _GENDER_ALIASES[text]
    except KeyError:
        raise ValueError(
            f'"{value}" is not a valid gender. Use one of: male, female, other.'
        )


# Documented format is ISO (YYYY-MM-DD); the rest are accepted because admins paste from
# spreadsheets. The Excel case matters most: openpyxl hands a date cell back as a datetime,
# which _clean_cell stringifies to 'YYYY-MM-DD 00:00:00' — the first format below matches it
# after the date part is split off, and the ISO format matches a plain CSV cell.
_DOB_FORMATS = ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y')


def normalize_dob(value: str) -> date | None:
    """Parse a date-of-birth cell into a date. Blank returns None (the column is optional).

    Raises ValueError, worded for the import report, when a non-empty cell can't be read as
    a date. The documented format is YYYY-MM-DD; a few common variants are also accepted.
    """
    text = (value or '').strip()
    if not text:
        return None
    # Excel dates arrive as 'YYYY-MM-DD 00:00:00' once stringified — drop the time part.
    text = text.split(' ')[0]
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f'"{value}" is not a valid date of birth. Use YYYY-MM-DD (e.g. 2015-03-21).'
    )


def read_import_rows(file_obj) -> tuple:
    """Parse an upload and vet its heading row.

    Returns (rows, error). `error` is a whole-file rejection message — the file could not
    be read at all, so no row was even attempted — and `rows` is empty whenever it is set.
    """
    try:
        rows = parse_upload_file(file_obj)
    except ValueError as exc:
        # Our own "this file is wrong, and here's why" messages.
        return [], str(exc)
    except Exception:
        # A corrupt or mislabelled file makes the underlying reader raise anything at all
        # (openpyxl throws BadZipFile at a renamed .xls). Uncaught, that is a 500 on what
        # is really just a bad upload — so every parse failure becomes a clean rejection.
        logger.exception('Unreadable bulk import file: %s', getattr(file_obj, 'name', '?'))
        return [], (
            'This file could not be read. It may be corrupt, or saved in a different '
            'format than its name suggests. Try re-saving it as .xlsx or CSV, or start '
            'from the template.'
        )

    if not rows:
        return [], (
            'The file has no data rows. Download the template, fill it in below the '
            'heading row, and upload it again.'
        )

    # Headings arrive already normalized (see file_parsers.normalize_header), so "Full
    # Name" and "full_name" both land here as full_name.
    present = set(rows[0].keys())
    missing = [column for column in REQUIRED_COLUMNS if column not in present]
    if missing:
        # Naming what the file *does* have is the point: the old message listed the
        # required columns as missing even when they were present under other spellings,
        # which read as the importer being broken.
        return [], (
            'Missing required column(s): {missing}. The file has: {found}. '
            'Download the template for the exact format.'.format(
                missing=', '.join(missing),
                found=', '.join(sorted(present)) or 'no columns',
            )
        )

    return rows, None


def validate_import_row(
    row: dict,
    seen_emails: dict,
    seen_rolls: dict,
    school_class: Class,
    section: Section,
) -> tuple:
    """Check one import row and return its cleaned values.

    Raises ValueError with a message written for the admin reading the import report —
    it names the row's problem, not the constraint that would have caught it later.

    The returned email is ``None``, never ``''``, when the cell is blank: students sign in
    with a Student ID, so an email is optional for them, and a blank one has to reach the
    database as NULL. Any number of rows may omit it.
    """
    email = row.get('email', '').strip().lower() or None
    full_name = row.get('full_name', '').strip()
    roll_number = row.get('roll_number', '').strip()

    blank = [
        name for name, value in (
            ('full_name', full_name), ('roll_number', roll_number),
        ) if not value
    ]
    if blank:
        raise ValueError(f'Missing required value(s): {", ".join(blank)}.')

    # Duplicates *within the file* would otherwise slip past the database checks below:
    # the first row commits, and the second fails on a constraint with a message that
    # never mentions that the file itself contains the same student twice.
    # Every email check below is guarded: without the guard, the second email-less row
    # would be reported as a duplicate of the first.
    if email and email in seen_emails:
        raise ValueError(f'Duplicate email in this file (also on row {seen_emails[email]}).')
    if roll_number in seen_rolls:
        raise ValueError(
            f'Duplicate roll number in this file (also on row {seen_rolls[roll_number]}).'
        )

    gender = normalize_gender(row.get('gender', ''))
    date_of_birth = normalize_dob(row.get('date_of_birth', ''))

    if email and User.objects.filter(email__iexact=email).exists():
        raise ValueError(f'A user with the email {email} already exists.')

    if StudentProfile.objects.filter(
        school_class=school_class, section=section, roll_number=roll_number,
    ).exists():
        raise ValueError(
            f'Roll number {roll_number} is already taken in this class and section.'
        )

    return email, full_name, roll_number, gender, date_of_birth


class BulkImportUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    school_class = serializers.PrimaryKeyRelatedField(queryset=Class.objects.all())
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all())

    def validate_file(self, value):
        name_lower = value.name.lower()
        if not name_lower.endswith(SUPPORTED_EXTENSIONS):
            raise serializers.ValidationError(
                f'Unsupported file format. Accepted: {", ".join(SUPPORTED_EXTENSIONS)}.'
            )
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError('File too large (max 5 MB).')
        return value

    def validate(self, attrs: dict) -> dict:
        school_class = attrs['school_class']
        section = attrs['section']
        if section.school_class_id != school_class.pk:
            raise serializers.ValidationError({'section': 'Section must belong to the selected class.'})
        return attrs

    def create(self, validated_data: dict) -> BulkImportLog:
        request = self.context['request']
        school = request.user.school
        if request.user.role == 'csc_admin':
            school = validated_data['school_class'].school

        school_class = validated_data['school_class']
        section = validated_data['section']
        file_obj = validated_data['file']

        import_log = BulkImportLog.objects.create(
            school=school,
            imported_by=request.user,
            file_name=file_obj.name,
            status=BulkImportLog.Status.PROCESSING,
        )

        def fail_import(message: str) -> BulkImportLog:
            """Whole-file rejection: nothing could be read, so no row was even attempted."""
            import_log.errors = [{'row': 0, 'error': message, 'level': 'error'}]
            import_log.status = BulkImportLog.Status.FAILED
            import_log.save()
            return import_log

        rows, file_error = read_import_rows(file_obj)
        if file_error is not None:
            return fail_import(file_error)

        import_log.total_rows = len(rows)
        unknown = sorted(set(rows[0].keys()) - set(ALL_COLUMNS))

        errors_list = []
        # Soft warnings: the row was imported, but something about it needs attention
        # (an unrecognised column). Keeping these out of fail_count is the difference
        # between "20 of 20 imported, 20 failed" and the truth.
        warnings_list = []
        # Login credentials for the students created by this upload, surfaced once in this
        # response so the admin can download and hand them out. Never persisted.
        credentials: list[dict] = []
        success = 0

        if unknown:
            warnings_list.append({
                'row': 0,
                'error': 'Ignored unrecognised column(s): {unknown}. Recognised: {known}.'.format(
                    unknown=', '.join(unknown), known=', '.join(ALL_COLUMNS),
                ),
                'level': 'warning',
            })

        seen_emails: dict[str, int] = {}
        seen_rolls: dict[str, int] = {}

        for idx, row in enumerate(rows, start=2):
            try:
                email, full_name, roll_number, gender, date_of_birth = validate_import_row(
                    row, seen_emails, seen_rolls, school_class, section,
                )

                # Generated here rather than inside the atomic block so the plaintext is
                # available to hand back for the credentials sheet below.
                student_id = generate_student_id(school)
                password = generate_student_password()

                with transaction.atomic():
                    # A bulk-imported student gets exactly what a hand-created one gets: a
                    # Student ID and a real, working password. They used to be created with
                    # an unusable password and an emailed setup link, which meant they could
                    # not sign in at all — and now that email is optional, there is often no
                    # inbox to send that link to. The credentials are returned once, below.
                    user = User.objects.create_user(
                        email=email,
                        password=password,
                        full_name=full_name,
                        role=User.Role.STUDENT,
                        school=school,
                        student_id=student_id,
                        is_password_set=True,
                    )
                    StudentProfile.objects.create(
                        user=user,
                        school=school,
                        school_class=school_class,
                        section=section,
                        roll_number=roll_number,
                        date_of_birth=date_of_birth,
                        admission_number=row.get('admission_number', '').strip(),
                        gender=gender,
                        parent_name=row.get('parent_name', '').strip(),
                        parent_phone=row.get('parent_phone', '').strip(),
                    )
                success += 1
                if email:
                    seen_emails[email] = idx
                seen_rolls[roll_number] = idx

                # Held in memory for this response only. These plaintext passwords are
                # never written to the import log, the audit log, or anywhere else.
                credentials.append({
                    'full_name': full_name,
                    'roll_number': roll_number,
                    'student_id': student_id,
                    'password': password,
                })
            except Exception as exc:
                errors_list.append({'row': idx, 'error': str(exc), 'level': 'error'})

        import_log.success_count = success
        import_log.fail_count = len(errors_list)
        # Warnings last, so the rows that actually need fixing are read first.
        import_log.errors = errors_list + warnings_list
        # Failed only when nothing at all got in; a partial import is completed-with-errors,
        # which the counts already describe.
        import_log.status = (
            BulkImportLog.Status.COMPLETED if success else BulkImportLog.Status.FAILED
        )
        import_log.save()

        # Transient attribute, read once by BulkImportLogSerializer for this response.
        # `import_log.save()` above has already run, and `credentials` is not a model
        # field — so no plaintext password reaches the database.
        import_log.credentials = credentials
        return import_log
