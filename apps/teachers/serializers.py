"""Serializers for teacher profiles and subject/class assignments."""
import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework import serializers

from apps.academics.models import Class, Section, Subject
from apps.schools.models import School

from .models import TeacherAssignment, TeacherProfile
from .services import generate_teacher_id

User = get_user_model()
logger = logging.getLogger(__name__)


class TeacherUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'full_name', 'is_active', 'last_login', 'created_at')
        read_only_fields = fields


class TeacherAssignmentReadSerializer(serializers.ModelSerializer):
    # default=None: subject is nullable (class-only assignments have none), and without a
    # default DRF raises walking `subject.name` on a null FK.
    subject_name = serializers.CharField(source='subject.name', read_only=True, default=None)
    class_name = serializers.CharField(source='school_class.name', read_only=True)
    section_name = serializers.CharField(source='section.name', read_only=True, default=None)

    class Meta:
        model = TeacherAssignment
        fields = (
            'id',
            'subject',
            'subject_name',
            'school_class',
            'class_name',
            'section',
            'section_name',
            'academic_year',
            'assigned_at',
        )
        read_only_fields = fields


class TeacherProfileListSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)
    # The human School ID (e.g. KAR_001) — the `code` column, never the school PK.
    school_code = serializers.CharField(source='school.code', read_only=True)
    assignments_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TeacherProfile
        fields = (
            'id',
            'user',
            'user_email',
            'user_name',
            'school',
            'school_name',
            'school_code',
            'teacher_id',
            'employee_id',
            'contact_number',
            'gender',
            'qualification',
            'joining_date',
            'is_active',
            'assignments_count',
        )
        read_only_fields = fields


class TeacherProfileDetailSerializer(serializers.ModelSerializer):
    user = TeacherUserSerializer(read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)
    # The human School ID (e.g. KAR_001) — the `code` column, never the school PK.
    school_code = serializers.CharField(source='school.code', read_only=True)
    assignments = TeacherAssignmentReadSerializer(many=True, read_only=True)
    # One-time password-setup link for the teacher. Present ONLY on the create response
    # (populated from a transient attribute) — always null on reads, exactly like a
    # School Admin's admin_setup_link.
    setup_link = serializers.SerializerMethodField()

    class Meta:
        model = TeacherProfile
        fields = (
            'id',
            'user',
            'school',
            'school_name',
            'school_code',
            'teacher_id',
            'employee_id',
            'contact_number',
            'gender',
            'qualification',
            'joining_date',
            'is_active',
            'assignments',
            'setup_link',
        )
        read_only_fields = (
            'id',
            'user',
            'school',
            'school_name',
            'school_code',
            'teacher_id',
            'employee_id',
            'contact_number',
            'gender',
            'qualification',
            'joining_date',
            'is_active',
            'assignments',
        )

    def get_setup_link(self, obj: TeacherProfile) -> str | None:
        return getattr(obj, '_setup_link', None)


class TeacherClassAssignmentSerializer(serializers.Serializer):
    """One class/section (and optionally subject) the teacher is assigned at creation time.

    `subject` stays optional: a school starts with no subjects at all, so requiring one
    would make the Add Teacher form unusable until subjects are set up. A subject, when
    given, must belong to the same class as the assignment.
    """

    school_class = serializers.PrimaryKeyRelatedField(queryset=Class.objects.all())
    section = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.all(), required=False, allow_null=True, default=None,
    )
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.all(), required=False, allow_null=True, default=None,
    )


class TeacherProfileWriteSerializer(serializers.Serializer):
    """Creates a User + TeacherProfile in one request. No password — teacher sets their own on first login."""
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=255)
    school = serializers.PrimaryKeyRelatedField(queryset=School.objects.all(), required=False)
    employee_id = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    contact_number = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    gender = serializers.ChoiceField(
        choices=TeacherProfile.Gender.choices,
        required=False,
        allow_blank=True,
        default='',
    )
    qualification = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    joining_date = serializers.DateField(required=False, allow_null=True, default=None)
    is_active = serializers.BooleanField(default=True)
    # One academic year for the teacher, stamped onto every assignment created below —
    # a teacher is onboarded for a given year, not per class.
    academic_year = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default='',
    )
    # Class/section/subject assignments made while creating the teacher. Write-only: reads
    # return the full rows via TeacherProfileDetailSerializer.assignments.
    assignments = TeacherClassAssignmentSerializer(
        many=True, required=False, write_only=True, default=list,
    )

    def validate_email(self, value: str) -> str:
        value = value.lower().strip()
        instance = self.context.get('instance')
        qs = User.objects.filter(email__iexact=value)
        if instance:
            qs = qs.exclude(pk=instance.user_id)
        if qs.exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_school(self, value: School) -> School:
        request = self.context.get('request')
        if request and request.user.role != 'csc_admin' and value.pk != request.user.school_id:
            raise serializers.ValidationError('You can only create teachers for your own school.')
        return value

    def validate_assignments(self, value: list) -> list:
        """Reject duplicates up front so the DB constraint never has to.

        Subject is part of the key: teaching both Maths and Science to Class 8-A is two
        legitimate assignments, but the same subject twice is not.
        """
        seen = set()
        for entry in value:
            section = entry.get('section')
            subject = entry.get('subject')
            key = (
                entry['school_class'].pk,
                section.pk if section else None,
                subject.pk if subject else None,
            )
            if key in seen:
                raise serializers.ValidationError(
                    'The same class, section and subject is assigned more than once.',
                )
            seen.add(key)
        return value

    def validate(self, attrs: dict) -> dict:
        """Create requires every profile field; assignments/year are create-only.

        Item 2: Add Teacher marks every field required. It is enforced here on CREATE only
        (``context['instance']`` is None) — NOT on update — so editing an existing teacher
        whose legacy field is blank is never blocked. This is a plain ``Serializer``, so the
        create/update signal is the context instance, never ``self.instance``.

        `update()` doesn't touch assignments, so accepting them here would silently discard
        the admin's edit. Existing teachers' assignments are managed via /teachers/assignments/.
        """
        if self.context.get('instance') is None:
            required_fields = ('employee_id', 'gender', 'qualification', 'joining_date')
            missing = {
                field: 'This field is required.'
                for field in required_fields
                if attrs.get(field) in (None, '')
            }
            if missing:
                raise serializers.ValidationError(missing)
            return attrs
        if attrs.get('assignments'):
            raise serializers.ValidationError({
                'assignments': 'Class assignments cannot be changed here. '
                               'Manage them from the teacher\'s assignments instead.',
            })
        if attrs.get('academic_year'):
            raise serializers.ValidationError({
                'academic_year': 'The academic year is set on the teacher\'s assignments. '
                                 'Manage them from the teacher\'s assignments instead.',
            })
        return attrs

    def _validate_assignment_scope(self, assignments: list, school: School) -> None:
        """Every class/section must belong to the teacher's own school.

        Checked here rather than trusting the client: the querysets above are unscoped, so
        without this a School Admin could assign their teacher to another school's class.
        """
        for entry in assignments:
            school_class = entry['school_class']
            section = entry.get('section')
            subject = entry.get('subject')
            if school_class.school_id != school.pk:
                raise serializers.ValidationError(
                    {'assignments': 'Class must belong to the teacher\'s school.'},
                )
            if section is not None and section.school_class_id != school_class.pk:
                raise serializers.ValidationError(
                    {'assignments': 'Section must belong to the selected class.'},
                )
            # A Subject row belongs to exactly one class ("Maths for Class 8" and "Maths
            # for Class 9" are different rows), so it must match the class being assigned.
            if subject is not None and subject.school_class_id != school_class.pk:
                raise serializers.ValidationError(
                    {'assignments': 'Subject must belong to the selected class.'},
                )

    def create(self, validated_data: dict) -> TeacherProfile:
        from apps.authentication.services import (
            EmailDeliveryError,
            build_password_setup_link,
            send_password_setup_email,
        )
        request = self.context['request']
        school = validated_data.pop('school', None)
        assignments = validated_data.pop('assignments', []) or []
        academic_year = validated_data.pop('academic_year', '') or ''
        if school is None:
            if request.user.role == 'csc_admin':
                raise serializers.ValidationError({'school': 'CSC Admin must specify a school.'})
            school = request.user.school

        # Validate before creating anything — the class/section scope check needs the
        # resolved school, which isn't known until here.
        self._validate_assignment_scope(assignments, school)

        user = User.objects.create_user(
            email=validated_data.pop('email'),
            password=None,
            full_name=validated_data.pop('full_name'),
            role=User.Role.TEACHER,
            school=school,
            is_password_set=False,
        )
        profile = TeacherProfile.objects.create(
            user=user,
            school=school,
            # Server-generated from the School ID. Not derived from employee_id, which stays
            # the school's own free-text reference.
            teacher_id=generate_teacher_id(school),
            employee_id=validated_data.get('employee_id', ''),
            contact_number=validated_data.get('contact_number', ''),
            gender=validated_data.get('gender', ''),
            qualification=validated_data.get('qualification', ''),
            joining_date=validated_data.get('joining_date'),
            is_active=validated_data.get('is_active', True),
        )

        # Class/section/subject assignments chosen on the Add Teacher form. The one
        # academic year from the form is stamped onto each of them.
        TeacherAssignment.objects.bulk_create([
            TeacherAssignment(
                teacher=profile,
                subject=entry.get('subject'),
                school_class=entry['school_class'],
                section=entry.get('section'),
                academic_year=academic_year,
                assigned_by=request.user,
            )
            for entry in assignments
        ])

        # Provision exactly like a School Admin: no password is chosen here. Email a one-time
        # setup link AND surface it on the create response so the admin can hand it over.
        # Delivery is best-effort — a mail failure must not undo the teacher we just created,
        # and the link stays valid regardless of delivery.
        from django.conf import settings as django_settings
        frontend_url = getattr(django_settings, 'FRONTEND_URL', 'http://localhost:5173')
        try:
            setup_link = send_password_setup_email(user, frontend_url=frontend_url)
        except EmailDeliveryError:
            setup_link = build_password_setup_link(user, frontend_url=frontend_url)
            logger.warning(
                'Teacher %s created but setup email failed to send; link returned to the '
                'admin instead.', user.email,
            )
        profile._setup_link = setup_link

        return profile

    def update(self, instance: TeacherProfile, validated_data: dict) -> TeacherProfile:
        user = instance.user
        if 'email' in validated_data:
            user.email = validated_data['email']
        if 'full_name' in validated_data:
            user.full_name = validated_data['full_name']
        if 'is_active' in validated_data:
            user.is_active = validated_data['is_active']
            instance.is_active = validated_data['is_active']
        user.save()

        for field in ('employee_id', 'gender', 'qualification', 'joining_date', 'contact_number'):
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.save()
        return instance


class TeacherStatsSerializer(serializers.Serializer):
    """Dashboard counters scoped to one teacher's own assignments."""

    assigned_classes = serializers.IntegerField()
    assigned_subjects = serializers.IntegerField()


class TeacherAssignmentWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeacherAssignment
        fields = ('teacher', 'subject', 'school_class', 'section', 'academic_year')

    def validate(self, attrs: dict) -> dict:
        teacher: TeacherProfile = attrs['teacher']
        # Subject is optional: a class-only assignment (no subject yet) is exactly what the
        # Add/Edit Teacher form creates, and the model FK is nullable. Reading it with `[]`
        # here would KeyError → 500 on that legitimate case.
        subject: Subject | None = attrs.get('subject')
        school_class: Class = attrs['school_class']
        section = attrs.get('section')

        if subject is not None and subject.school_id != teacher.school_id:
            raise serializers.ValidationError({'subject': 'Subject must belong to the same school as the teacher.'})
        if school_class.school_id != teacher.school_id:
            raise serializers.ValidationError({'school_class': 'Class must belong to the same school as the teacher.'})
        if section is not None and section.school_class_id != school_class.pk:
            raise serializers.ValidationError({'section': 'Section must belong to the selected class.'})
        # A Subject row belongs to exactly one class, so it must match the class assigned.
        if subject is not None and subject.school_class_id != school_class.pk:
            raise serializers.ValidationError(
                {'subject': 'Subject must belong to the selected class.'},
            )

        # Reject duplicates here so the unique constraints never surface as a 500. The
        # matching key MUST mirror the constraints: subject-bound rows are unique per
        # `academic_year` (the unique_together includes it), but the partial constraints for
        # class-only rows (subject IS NULL) DON'T include the year — so a subject-less
        # Class 8-A "2025-26" and "2026-27" collide. Including the year in the class-only
        # check would miss that and let it 500 at the DB.
        duplicate = TeacherAssignment.objects.filter(
            teacher=teacher,
            subject=subject,
            school_class=school_class,
            section=section,
        )
        if subject is not None:
            duplicate = duplicate.filter(academic_year=attrs.get('academic_year', ''))
        if self.instance is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError('This class is already assigned to the teacher.')
        return attrs

    def create(self, validated_data: dict) -> TeacherAssignment:
        validated_data['assigned_by'] = self.context['request'].user
        # Savepoint backstop: the validate() pre-check catches the ordinary duplicate, but a
        # concurrent insert could still trip a unique constraint. Under ATOMIC_REQUESTS a raw
        # IntegrityError poisons the request transaction, so it must be caught inside a
        # savepoint and turned into a clean 400 rather than a 500.
        try:
            with transaction.atomic():
                return super().create(validated_data)
        except IntegrityError:
            raise serializers.ValidationError('This class is already assigned to the teacher.')
