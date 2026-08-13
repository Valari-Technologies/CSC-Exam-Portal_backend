"""Serializers for online exam sessions, answers, and anti-cheat events."""
import random

from django.utils import timezone
from rest_framework import serializers

from apps.students.models import StudentProfile
from apps.tests.models import Test, TestAssignment, TestAssignmentStudent, TestQuestion

from .models import AntiCheatLog, ExamAnswer, ExamSession

# The canonical option order. `shuffle_options` permutes this per question for display;
# the letters themselves never change meaning.
OPTION_KEYS = ('a', 'b', 'c', 'd')


# ---------------------------------------------------------------------------
# Nested helpers
# ---------------------------------------------------------------------------

class _StudentBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    full_name = serializers.CharField()


class _ExamAnswerSerializer(serializers.ModelSerializer):
    question_id = serializers.IntegerField(source='question.id')
    question_text = serializers.CharField(source='question.question_text', read_only=True)

    class Meta:
        model = ExamAnswer
        fields = (
            'id',
            'question_id',
            'question_text',
            'selected_option',
            'time_spent_seconds',
            'last_updated_at',
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# List / Detail
# ---------------------------------------------------------------------------

class ExamSessionListSerializer(serializers.ModelSerializer):
    """Rich list row for the teacher evaluation dashboard.

    The ViewSet queryset must select_related
    student__student_profile__school_class / section, test__subject and result
    so these fields never trigger per-row queries.
    """

    student_email = serializers.EmailField(source='student.email', read_only=True, allow_null=True)
    student_name = serializers.CharField(source='student.full_name', read_only=True)
    test_title = serializers.CharField(source='test.title', read_only=True)
    subject_name = serializers.CharField(source='test.subject.name', read_only=True)
    lesson_name = serializers.SerializerMethodField()
    class_name = serializers.SerializerMethodField()
    section_name = serializers.SerializerMethodField()
    obtained_marks = serializers.SerializerMethodField()
    total_marks = serializers.SerializerMethodField()
    percentage = serializers.SerializerMethodField()
    result_id = serializers.SerializerMethodField()
    is_published = serializers.SerializerMethodField()

    class Meta:
        model = ExamSession
        fields = (
            'id',
            'student',
            'student_email',
            'student_name',
            'class_name',
            'section_name',
            'test',
            'test_title',
            'subject_name',
            'lesson_name',
            'assignment',
            'status',
            'started_at',
            'submitted_at',
            'time_remaining_seconds',
            'obtained_marks',
            'total_marks',
            'percentage',
            'result_id',
            'is_published',
        )
        read_only_fields = fields

    def _profile(self, obj: ExamSession):
        try:
            return obj.student.student_profile
        except StudentProfile.DoesNotExist:
            return None

    def get_class_name(self, obj: ExamSession) -> str | None:
        profile = self._profile(obj)
        return profile.school_class.name if profile and profile.school_class_id else None

    def get_section_name(self, obj: ExamSession) -> str | None:
        profile = self._profile(obj)
        return profile.section.name if profile and profile.section_id else None

    def get_lesson_name(self, obj: ExamSession) -> str | None:
        first_tq = obj.test.test_questions.first()
        if first_tq and first_tq.question:
            return first_tq.question.lesson
        return None

    def _result(self, obj: ExamSession):
        # OneToOne reverse — select_related('result') makes this free.
        try:
            return obj.result
        except ExamSession.result.RelatedObjectDoesNotExist:
            return None

    def get_obtained_marks(self, obj: ExamSession) -> str | None:
        result = self._result(obj)
        return str(result.obtained_marks) if result else None

    def get_total_marks(self, obj: ExamSession) -> str | None:
        result = self._result(obj)
        return str(result.total_marks) if result else None

    def get_percentage(self, obj: ExamSession) -> str | None:
        result = self._result(obj)
        return str(result.percentage) if result else None

    def get_result_id(self, obj: ExamSession) -> int | None:
        result = self._result(obj)
        return result.pk if result else None

    def get_is_published(self, obj: ExamSession) -> bool:
        result = self._result(obj)
        return bool(result.is_published) if result else False


class ExamSessionDetailSerializer(serializers.ModelSerializer):
    student = _StudentBriefSerializer(read_only=True)
    test_title = serializers.CharField(source='test.title', read_only=True)
    test_duration_minutes = serializers.IntegerField(source='test.duration_minutes', read_only=True)
    answers = _ExamAnswerSerializer(many=True, read_only=True)
    questions = serializers.SerializerMethodField()

    class Meta:
        model = ExamSession
        fields = (
            'id',
            'student',
            'test',
            'test_title',
            'test_duration_minutes',
            'assignment',
            'status',
            'started_at',
            'submitted_at',
            'time_remaining_seconds',
            'ip_address',
            'browser_info',
            'answers',
            'questions',
        )
        read_only_fields = fields

    def get_questions(self, obj: ExamSession) -> list[dict]:
        """Return questions for the exam — without correct_option.

        Honours the test's `shuffle_questions` / `shuffle_options` settings. Both shuffles
        are seeded from the session id, which makes them STABLE: this method is the single
        delivery point for both `start` and `retrieve`, so a student who refreshes mid-exam
        sees the same order they started with, while two students get different orders. No
        permutation needs storing — the seed reproduces it.

        Options are reordered for DISPLAY ONLY: each option keeps its identity letter and
        travels in `option_order`. `selected_option` therefore still means the same thing it
        always did, and grading (which compares it to `correct_option`) is untouched.
        """
        test = obj.test
        test_questions = list(
            TestQuestion.objects
            .filter(test_id=obj.test_id)
            .select_related('question')
            .order_by('order_number')
        )

        if test.shuffle_questions:
            random.Random(obj.pk).shuffle(test_questions)

        result = []
        for tq in test_questions:
            q = tq.question
            option_order = list(OPTION_KEYS)
            if test.shuffle_options:
                # Seeded per question as well, so one question's order does not depend on
                # how many questions happen to precede it.
                random.Random(f'{obj.pk}-{q.id}').shuffle(option_order)
            result.append({
                'option_order': option_order,
                'id': q.id,
                'question_text': q.question_text,
                'question_image': q.question_image.url if q.question_image else None,
                'option_a': q.option_a,
                'option_b': q.option_b,
                'option_c': q.option_c,
                'option_d': q.option_d,
                'option_a_image': q.option_a_image.url if q.option_a_image else None,
                'option_b_image': q.option_b_image.url if q.option_b_image else None,
                'option_c_image': q.option_c_image.url if q.option_c_image else None,
                'option_d_image': q.option_d_image.url if q.option_d_image else None,
                'marks': float(tq.marks_override if tq.marks_override is not None else q.marks),
                'order_number': tq.order_number,
            })
        return result


# ---------------------------------------------------------------------------
# Start exam
# ---------------------------------------------------------------------------

class StartExamSerializer(serializers.Serializer):
    """Validate and start an exam session for the requesting student."""
    assignment_id = serializers.IntegerField()

    def validate_assignment_id(self, value: int) -> int:
        try:
            assignment = TestAssignment.objects.select_related('test').get(pk=value)
        except TestAssignment.DoesNotExist:
            raise serializers.ValidationError('Assignment not found.')

        self._assignment = assignment
        return value

    def validate(self, attrs: dict) -> dict:
        request = self.context['request']
        user = request.user
        assignment: TestAssignment = self._assignment
        test: Test = assignment.test

        # 1. Test must be published
        if test.status != Test.Status.PUBLISHED:
            raise serializers.ValidationError({'assignment_id': 'This test is not published.'})

        # 2. Assignment must be active
        if not assignment.is_active:
            raise serializers.ValidationError({'assignment_id': 'This assignment is no longer active.'})

        # 3. Within time window
        now = timezone.now()
        if now < assignment.start_datetime:
            raise serializers.ValidationError({'assignment_id': 'This exam has not started yet.'})
        if now > assignment.end_datetime:
            raise serializers.ValidationError({'assignment_id': 'This exam window has closed.'})

        # 4. Student is assigned — depends on assigned_to_type
        self._validate_student_assigned(user, assignment)

        # 5. No active session for this assignment
        if ExamSession.objects.filter(
            student=user,
            assignment=assignment,
            status__in=[ExamSession.Status.STARTED, ExamSession.Status.IN_PROGRESS],
        ).exists():
            raise serializers.ValidationError(
                {'assignment_id': 'You already have an active exam session for this assignment.'}
            )

        # 6. A finished attempt is final.
        #
        # This is NOT an attempts limit and no longer reads
        # `assignment.max_attempts` — availability is controlled solely by the
        # start/end window above. It is an integrity rule: a submitted attempt
        # has already produced a Result row, which may have been evaluated,
        # ranked and published. Letting the same student sit the same assignment
        # again would create a second Result for them in one cohort, so they
        # would appear twice in the class list and occupy two places in the
        # ranking, and the teacher's published marks would silently change
        # underneath them.
        #
        # A genuine retake is a new assignment — that also gives the retake its
        # own window and its own cohort, which is what ranking is scoped to.
        if ExamSession.objects.filter(
            student=user,
            assignment=assignment,
            status__in=[
                ExamSession.Status.SUBMITTED,
                ExamSession.Status.EVALUATED,
                ExamSession.Status.PUBLISHED,
                ExamSession.Status.ABANDONED,
            ],
        ).exists():
            raise serializers.ValidationError(
                {'assignment_id': 'You have already completed this exam.'}
            )

        attrs['assignment'] = assignment
        attrs['test'] = test
        return attrs

    def _validate_student_assigned(self, user, assignment: TestAssignment) -> None:
        """Ensure the student is part of the target audience for this assignment."""
        try:
            profile = StudentProfile.objects.get(user=user)
        except StudentProfile.DoesNotExist:
            raise serializers.ValidationError(
                {'assignment_id': 'Student profile not found.'}
            )

        if assignment.assigned_to_type == TestAssignment.AssignedToType.CLASS:
            if profile.school_class_id != assignment.school_class_id:
                raise serializers.ValidationError(
                    {'assignment_id': 'You are not assigned to this exam.'}
                )
        elif assignment.assigned_to_type == TestAssignment.AssignedToType.SECTION:
            if profile.section_id != assignment.section_id:
                raise serializers.ValidationError(
                    {'assignment_id': 'You are not assigned to this exam.'}
                )
        elif assignment.assigned_to_type == TestAssignment.AssignedToType.STUDENTS:
            if not TestAssignmentStudent.objects.filter(
                assignment=assignment, student=user,
            ).exists():
                raise serializers.ValidationError(
                    {'assignment_id': 'You are not assigned to this exam.'}
                )


# ---------------------------------------------------------------------------
# Save answer
# ---------------------------------------------------------------------------

class SaveAnswerSerializer(serializers.Serializer):
    """Save a student's answer to a single question."""
    question_id = serializers.IntegerField()
    selected_option = serializers.ChoiceField(
        choices=['a', 'b', 'c', 'd'],
        allow_null=True,
        required=False,
    )
    time_spent_seconds = serializers.IntegerField(min_value=0, required=False, default=0)
    time_remaining_seconds = serializers.IntegerField(min_value=0, required=False)

    def validate_question_id(self, value: int) -> int:
        session: ExamSession = self.context['session']
        try:
            answer = ExamAnswer.objects.get(session=session, question_id=value)
        except ExamAnswer.DoesNotExist:
            raise serializers.ValidationError('This question is not part of this exam session.')
        self._answer = answer
        return value


# ---------------------------------------------------------------------------
# Submit exam
# ---------------------------------------------------------------------------

class SubmitExamSerializer(serializers.Serializer):
    """No input fields — triggers exam submission."""
    pass


# ---------------------------------------------------------------------------
# Anti-cheat event
# ---------------------------------------------------------------------------

class AntiCheatEventSerializer(serializers.Serializer):
    event_type = serializers.ChoiceField(
        choices=AntiCheatLog.EventType.choices,
    )
