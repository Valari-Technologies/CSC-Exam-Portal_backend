"""Serializers for exam results and per-question breakdowns."""
import logging

from rest_framework import serializers

from .models import Result, ResultDetail

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Nested helpers
# ---------------------------------------------------------------------------

class _ResultDetailSerializer(serializers.ModelSerializer):
    question_id = serializers.IntegerField(source='question.id', read_only=True)
    question_text = serializers.CharField(source='question.question_text', read_only=True)

    class Meta:
        model = ResultDetail
        fields = (
            'id',
            'question_id',
            'question_text',
            'selected_option',
            'correct_option',
            'is_correct',
            'marks_obtained',
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# List / Detail
# ---------------------------------------------------------------------------

class ResultListSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.full_name', read_only=True)
    student_email = serializers.EmailField(source='student.email', read_only=True, allow_null=True)
    test_title = serializers.CharField(source='test.title', read_only=True)
    subject_name = serializers.CharField(source='test.subject.name', read_only=True)
    lesson_name = serializers.SerializerMethodField()
    class_name = serializers.SerializerMethodField()
    section_name = serializers.SerializerMethodField()

    class Meta:
        model = Result
        fields = (
            'id',
            'student',
            'student_name',
            'student_email',
            'class_name',
            'section_name',
            'test',
            'test_title',
            'subject_name',
            'lesson_name',
            'assignment',
            'obtained_marks',
            'total_marks',
            'percentage',
            'passed',
            'rank',
            'is_published',
            'published_at',
            'calculated_at',
        )
        read_only_fields = fields

    def _profile(self, obj: Result):
        try:
            return obj.student.student_profile
        except Exception:
            return None

    def get_class_name(self, obj: Result) -> str | None:
        profile = self._profile(obj)
        return profile.school_class.name if profile and profile.school_class_id else None

    def get_section_name(self, obj: Result) -> str | None:
        profile = self._profile(obj)
        return profile.section.name if profile and profile.section_id else None

    def get_lesson_name(self, obj: Result) -> str | None:
        first_tq = obj.test.test_questions.first()
        if first_tq and first_tq.question:
            return first_tq.question.lesson
        return None


class ResultDetailSerializer(serializers.ModelSerializer):
    """Full result. `details` is the per-question breakdown — the "review".

    A test with `allow_review_after_submit=False` withholds that breakdown from the
    STUDENT: they still see their score, pass/fail and counts, just not which question
    they got wrong. Teachers and admins always see it — the setting governs what the
    student may review, not what staff may evaluate.
    """

    student_name = serializers.CharField(source='student.full_name', read_only=True)
    student_email = serializers.EmailField(source='student.email', read_only=True, allow_null=True)
    test_title = serializers.CharField(source='test.title', read_only=True)
    details = serializers.SerializerMethodField()
    review_allowed = serializers.SerializerMethodField()

    def _review_allowed(self, obj: Result) -> bool:
        request = self.context.get('request')
        # No request in context (internal use) → behave as staff and include everything.
        if request is None or getattr(request.user, 'role', None) != 'student':
            return True
        return obj.test.allow_review_after_submit

    def get_review_allowed(self, obj: Result) -> bool:
        """Lets the UI say "review disabled" rather than render an empty list."""
        return self._review_allowed(obj)

    def get_details(self, obj: Result) -> list:
        if not self._review_allowed(obj):
            return []
        return _ResultDetailSerializer(obj.details.all(), many=True).data

    class Meta:
        model = Result
        fields = (
            'id',
            'session',
            'student',
            'student_name',
            'student_email',
            'test',
            'test_title',
            'assignment',
            'total_marks',
            'obtained_marks',
            'percentage',
            'passed',
            'correct_count',
            'wrong_count',
            'unattempted_count',
            'rank',
            'time_taken_seconds',
            'is_published',
            'published_at',
            'calculated_at',
            'details',
            'review_allowed',
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

class PublishResultSerializer(serializers.Serializer):
    """Toggle is_published on a single result."""
    is_published = serializers.BooleanField()
