"""Serializers for Test, TestQuestion, and TestAssignment resources."""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from apps.academics.models import Class, Section, Subject
from apps.questions.models import Question

from .models import Test, TestAssignment, TestAssignmentStudent, TestQuestion

User = get_user_model()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TestQuestion serializers
# ---------------------------------------------------------------------------

class TestQuestionSerializer(serializers.ModelSerializer):
    """Read serializer — includes basic question info for display."""

    question_text = serializers.CharField(source='question.question_text', read_only=True)
    difficulty = serializers.CharField(source='question.difficulty', read_only=True)
    marks = serializers.DecimalField(
        source='question.marks', max_digits=5, decimal_places=2, read_only=True,
    )

    class Meta:
        model = TestQuestion
        fields = (
            'id',
            'question',
            'question_text',
            'order_number',
            'marks_override',
            'difficulty',
            'marks',
        )
        read_only_fields = fields


class TestQuestionWriteSerializer(serializers.Serializer):
    """Payload for bulk-adding questions to a test."""

    question_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        help_text='List of Question PKs to add.',
    )

    def validate_question_ids(self, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise serializers.ValidationError('Duplicate question IDs in request.')
        return value


class AutoGenerateSerializer(serializers.Serializer):
    """Payload for auto-generating questions from the question bank."""

    count = serializers.IntegerField(
        min_value=1,
        help_text='Number of questions to auto-generate.',
    )
    difficulty = serializers.ChoiceField(
        choices=Question.Difficulty.choices,
        required=False,
        allow_blank=True,
        help_text='Optional difficulty filter: easy, medium, or hard.',
    )
    chapter_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text='Optional list of Chapter PKs to filter by.',
    )


# ---------------------------------------------------------------------------
# Test serializers
# ---------------------------------------------------------------------------

class TestListSerializer(serializers.ModelSerializer):
    """Compact representation for list views."""

    subject_name = serializers.CharField(source='subject.name', read_only=True)
    class_name = serializers.CharField(source='school_class.name', read_only=True)
    question_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Test
        fields = (
            'id',
            'title',
            'subject_name',
            'class_name',
            'total_marks',
            'duration_minutes',
            'status',
            'question_count',
            'created_at',
        )
        read_only_fields = fields


class TestDetailSerializer(serializers.ModelSerializer):
    """Full representation including nested questions and assignment count."""

    subject_name = serializers.CharField(source='subject.name', read_only=True)
    class_name = serializers.CharField(source='school_class.name', read_only=True)
    created_by_name = serializers.CharField(
        source='created_by.full_name', read_only=True, default=None,
    )
    questions = TestQuestionSerializer(source='test_questions', many=True, read_only=True)
    assignments_count = serializers.SerializerMethodField()

    class Meta:
        model = Test
        fields = (
            'id',
            'school',
            'created_by',
            'created_by_name',
            'title',
            'description',
            'subject',
            'subject_name',
            'school_class',
            'class_name',
            'total_marks',
            'passing_marks',
            'duration_minutes',
            'instructions',
            'shuffle_questions',
            'shuffle_options',
            'show_result_immediately',
            'allow_review_after_submit',
            'status',
            'questions',
            'assignments_count',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    def get_assignments_count(self, obj: Test) -> int:
        return obj.assignments.count()


class TestWriteSerializer(serializers.ModelSerializer):
    """Create / update a Test. Auto-derives school from the request user."""

    class Meta:
        model = Test
        fields = (
            'title',
            'description',
            'subject',
            'school_class',
            'total_marks',
            'passing_marks',
            'duration_minutes',
            'instructions',
            'shuffle_questions',
            'shuffle_options',
            'show_result_immediately',
            'allow_review_after_submit',
        )
        extra_kwargs = {
            # Both fields carry a model default of 0, which makes DRF treat them as
            # optional — a POST omitting them silently created a test worth 0 marks.
            # Class, subject and duration are already required (FKs with no default,
            # and duration_minutes has no default either).
            'total_marks': {'required': True},
            'passing_marks': {'required': True},
        }

    def validate(self, attrs: dict) -> dict:
        request = self.context.get('request')
        subject: Subject | None = attrs.get('subject')
        school_class: Class | None = attrs.get('school_class')

        # Determine school
        if request and request.user.role == 'csc_admin':
            if school_class:
                school = school_class.school
            elif subject:
                school = subject.school
            elif self.instance:
                school = self.instance.school
            else:
                raise serializers.ValidationError(
                    'CSC Admin: unable to derive school — provide subject or school_class.',
                )
        elif request:
            school = request.user.school
        else:
            raise serializers.ValidationError('Unable to determine school.')

        # Validate subject belongs to the same school
        if subject and subject.school_id != school.pk:
            raise serializers.ValidationError(
                {'subject': 'Subject must belong to the same school.'},
            )

        # Validate school_class belongs to the same school
        if school_class and school_class.school_id != school.pk:
            raise serializers.ValidationError(
                {'school_class': 'Class must belong to the same school.'},
            )

        # Validate subject belongs to the selected class
        if subject and school_class and subject.school_class_id != school_class.pk:
            raise serializers.ValidationError(
                {'subject': 'Subject must belong to the selected class.'},
            )

        # Validate passing_marks <= total_marks
        total = attrs.get('total_marks', getattr(self.instance, 'total_marks', 0))
        passing = attrs.get('passing_marks', getattr(self.instance, 'passing_marks', 0))

        # A zero-mark test is never valid. This mirrors the form rule — without it, a
        # blank Total Marks that coerces to 0 on the way through would still be accepted
        # by the API. Passing marks of 0 is left alone: "everyone passes" is a real choice.
        if 'total_marks' in attrs and total <= 0:
            raise serializers.ValidationError(
                {'total_marks': 'Total marks must be greater than 0.'},
            )

        if passing > total:
            raise serializers.ValidationError(
                {'passing_marks': 'Passing marks cannot exceed total marks.'},
            )

        attrs['_school'] = school
        return attrs

    def update(self, instance: Test, validated_data: dict) -> Test:
        validated_data.pop('_school', None)
        return super().update(instance, validated_data)


# ---------------------------------------------------------------------------
# TestAssignment serializers
# ---------------------------------------------------------------------------

class TestAssignmentListSerializer(serializers.ModelSerializer):
    """Compact representation for assignment list views."""

    availability = serializers.SerializerMethodField()
    test_title = serializers.CharField(source='test.title', read_only=True)
    class_name = serializers.CharField(
        source='school_class.name', read_only=True, default=None,
    )
    section_name = serializers.CharField(
        source='section.name', read_only=True, default=None,
    )

    class Meta:
        model = TestAssignment
        fields = (
            'id',
            'test',
            'test_title',
            'assigned_to_type',
            'school_class',
            'class_name',
            'section',
            'section_name',
            'start_datetime',
            'end_datetime',
            'availability',
            'is_active',
            'assigned_at',
        )
        read_only_fields = fields

    def get_availability(self, obj: TestAssignment) -> str:
        """Where this assignment sits in its window, decided by the SERVER clock.

        The student page can compare `now` to the two datetimes itself — and it
        does, once a second, so a card left open transitions on its own. What it
        cannot do is be sure its own clock is right: a device set an hour slow
        would draw "Start Exam" on a window that closed an hour ago. The start
        endpoint would still refuse, so this was never a way in, but the button
        lied about it.

        One of 'upcoming', 'open', 'expired'.
        """
        now = timezone.now()
        if now < obj.start_datetime:
            return 'upcoming'
        if now > obj.end_datetime:
            return 'expired'
        return 'open'


class TestAssignmentWriteSerializer(serializers.ModelSerializer):
    """Create / update a TestAssignment with date and FK validation."""

    # Only used when assigned_to_type == 'students': the explicit recipients.
    students = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.filter(role='student', is_active=True),
        required=False,
        write_only=True,
    )

    class Meta:
        model = TestAssignment
        fields = (
            'test',
            'assigned_to_type',
            'school_class',
            'section',
            'students',
            'start_datetime',
            'end_datetime',
        )

    def _validate_class_assignment(self, school_class, school_id):
        if not school_class:
            raise serializers.ValidationError(
                {'school_class': 'Class is required for class-level assignment.'},
            )
        if school_id and school_class.school_id != school_id:
            raise serializers.ValidationError(
                {'school_class': 'Class must belong to the same school as the test.'},
            )

    def _validate_section_assignment(self, school_class, section, school_id):
        if not school_class:
            raise serializers.ValidationError(
                {'school_class': 'Class is required for section-level assignment.'},
            )
        if not section:
            raise serializers.ValidationError(
                {'section': 'Section is required for section-level assignment.'},
            )
        if school_id and school_class.school_id != school_id:
            raise serializers.ValidationError(
                {'school_class': 'Class must belong to the same school as the test.'},
            )
        if section.school_class_id != school_class.pk:
            raise serializers.ValidationError(
                {'section': 'Section must belong to the selected class.'},
            )

    def _validate_student_assignment(self, students, school_id):
        if not students:
            raise serializers.ValidationError(
                {'students': 'At least one student is required for student-level assignment.'},
            )
        if school_id:
            wrong = [s.email for s in students if s.school_id != school_id]
            if wrong:
                raise serializers.ValidationError(
                    {
                        'students': (
                            'Students must belong to the same school as the test: '
                            f'{", ".join(wrong)}'
                        )
                    },
                )

    def validate(self, attrs: dict) -> dict:
        request = self.context.get('request')
        test: Test = attrs.get('test') or (self.instance.test if self.instance else None)
        assigned_to_type = attrs.get(
            'assigned_to_type',
            getattr(self.instance, 'assigned_to_type', None),
        )
        school_class: Class | None = attrs.get('school_class')
        section: Section | None = attrs.get('section')
        start_dt = attrs.get('start_datetime')
        end_dt = attrs.get('end_datetime')

        # Date validation
        if start_dt and end_dt and end_dt <= start_dt:
            raise serializers.ValidationError(
                {'end_datetime': 'End datetime must be after start datetime.'},
            )

        # Determine school from test
        if test:
            school_id = test.school_id
        elif request:
            school_id = getattr(request.user, 'school_id', None)
        else:
            school_id = None

        # Class-level assignment validation
        if assigned_to_type == TestAssignment.AssignedToType.CLASS:
            self._validate_class_assignment(school_class, school_id)

        # Section-level assignment validation
        if assigned_to_type == TestAssignment.AssignedToType.SECTION:
            self._validate_section_assignment(school_class, section, school_id)

        # Individual-student assignment validation
        students = attrs.get('students') or []
        if assigned_to_type == TestAssignment.AssignedToType.STUDENTS:
            self._validate_student_assignment(students, school_id)
        elif students:
            raise serializers.ValidationError(
                {'students': 'students may only be supplied for student-level assignment.'},
            )

        return attrs

    def create(self, validated_data: dict) -> TestAssignment:
        # 'students' is not a model field — pop it and persist via the
        # TestAssignmentStudent through model. Rows must exist before the
        # notification fan-out in the view resolves recipients.
        students = validated_data.pop('students', [])
        assignment = super().create(validated_data)
        if students:
            TestAssignmentStudent.objects.bulk_create(
                [TestAssignmentStudent(assignment=assignment, student=s) for s in students],
                batch_size=500,
            )
        return assignment

    def update(self, instance: TestAssignment, validated_data: dict) -> TestAssignment:
        validated_data.pop('students', None)  # recipients are immutable after creation
        return super().update(instance, validated_data)
