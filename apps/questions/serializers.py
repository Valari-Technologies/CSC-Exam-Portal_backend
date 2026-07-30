"""Serializers for the Question Bank module.

Four serializers:
- QuestionListSerializer: compact list view with nested subject/chapter {id, name}.
- QuestionDetailSerializer: full detail view with denormalised names.
- QuestionWriteSerializer: create/update with cross-FK validation.
- QuestionBulkImportSerializer: bulk import from CSV/Excel files.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework import serializers

from apps.academics.models import Chapter, Subject
from apps.schools.models import School

from .bulk_import import parse_questions_file, validate_required_columns
from .models import Question

logger = logging.getLogger(__name__)


class QuestionListSerializer(serializers.ModelSerializer):
    """Compact representation for paginated list views."""

    subject = serializers.SerializerMethodField()
    chapter = serializers.SerializerMethodField()
    question_text = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = (
            'id',
            'school',
            'subject',
            'chapter',
            'question_text',
            'difficulty',
            'marks',
            'correct_option',
            'is_active',
            'created_at',
        )

    def get_subject(self, obj: Question) -> dict[str, int | str]:
        return {'id': obj.subject_id, 'name': obj.subject.name}

    def get_chapter(self, obj: Question) -> dict[str, int | str]:
        return {'id': obj.chapter_id, 'name': obj.chapter.name}

    def get_question_text(self, obj: Question) -> str:
        text = obj.question_text
        if len(text) > 100:
            return text[:100] + '…'
        return text


class QuestionDetailSerializer(serializers.ModelSerializer):
    """Full detail view — all fields plus denormalised names."""

    subject_name = serializers.CharField(source='subject.name', read_only=True)
    chapter_name = serializers.CharField(source='chapter.name', read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = (
            'id',
            'school',
            'subject',
            'subject_name',
            'chapter',
            'chapter_name',
            'created_by',
            'created_by_name',
            'question_text',
            'question_image',
            'option_a',
            'option_b',
            'option_c',
            'option_d',
            'option_a_image',
            'option_b_image',
            'option_c_image',
            'option_d_image',
            'correct_option',
            'explanation',
            'difficulty',
            'marks',
            'negative_marks',
            'is_active',
            'created_at',
            'updated_at',
        )

    def get_created_by_name(self, obj: Question) -> str | None:
        if obj.created_by:
            return obj.created_by.full_name
        return None


class QuestionWriteSerializer(serializers.ModelSerializer):
    """Create / update serializer with cross-FK consistency checks."""

    class Meta:
        model = Question
        fields = (
            'subject',
            'chapter',
            'question_text',
            'question_image',
            'option_a',
            'option_b',
            'option_c',
            'option_d',
            'option_a_image',
            'option_b_image',
            'option_c_image',
            'option_d_image',
            'correct_option',
            'explanation',
            'difficulty',
            'marks',
            'negative_marks',
            'is_active',
        )

    def validate_correct_option(self, value: str) -> str:
        if value not in ('a', 'b', 'c', 'd'):
            raise serializers.ValidationError('correct_option must be one of: a, b, c, d.')
        return value

    def validate(self, attrs: dict) -> dict:
        subject = attrs.get('subject') or (self.instance.subject if self.instance else None)
        chapter = attrs.get('chapter') or (self.instance.chapter if self.instance else None)

        if subject is None:
            raise serializers.ValidationError({'subject': 'Subject is required.'})
        if chapter is None:
            raise serializers.ValidationError({'chapter': 'Chapter is required.'})

        # Chapter must belong to the selected subject
        if chapter.subject_id != subject.id:
            raise serializers.ValidationError(
                {'chapter': 'Chapter does not belong to the selected subject.'}
            )

        # School-scope validation
        request = self.context.get('request')
        if request:
            if request.user.role == 'csc_admin':
                # CSC Admin must provide a school, and it must match the subject's school
                school_id = request.data.get('school')
                if school_id is not None and int(school_id) != subject.school_id:
                    raise serializers.ValidationError(
                        {'school': 'School does not match the subject\'s school.'}
                    )
            else:
                user_school_id = request.user.school_id
                if subject.school_id != user_school_id:
                    raise serializers.ValidationError(
                        {'subject': 'Subject belongs to a different school.'}
                    )

        return attrs


class QuestionBulkDeleteSerializer(serializers.Serializer):
    """Validate the ``ids`` payload for the bulk-delete action.

    Only shape is checked here; ownership is enforced in the view by filtering the
    ids through the school-scoped queryset.
    """

    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=500,
    )

    def validate_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))  # de-duplicate, preserve order


class QuestionBulkImportSerializer(serializers.Serializer):
    """Bulk import questions from a CSV or Excel file.

    Accepts a ``file`` and optional ``school`` (required for CSC Admin).
    Parses rows, validates each one, and creates Question objects.
    Returns an import summary.
    """

    file = serializers.FileField()
    school = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.all(), required=False,
    )

    def validate_file(self, value):
        name_lower = value.name.lower()
        accepted = ('.csv', '.xlsx', '.xls', '.json', '.txt', '.docx')
        if not name_lower.endswith(accepted):
            raise serializers.ValidationError(
                f'Unsupported file format. Accepted: {", ".join(accepted)}.'
            )
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('File too large (max 10 MB).')
        return value

    def validate(self, attrs: dict) -> dict:
        request = self.context.get('request')
        if request and request.user.role == 'csc_admin' and not attrs.get('school'):
            raise serializers.ValidationError(
                {'school': 'CSC Admin must specify a school for bulk import.'}
            )
        return attrs

    def create(self, validated_data: dict) -> dict:
        request = self.context['request']
        user = request.user
        file_obj = validated_data['file']

        # Determine school
        if user.role == 'csc_admin':
            school = validated_data['school']
        else:
            school = user.school

        # Parse the file
        try:
            rows = parse_questions_file(file_obj)
        except ValueError as exc:
            return {
                'total': 0,
                'success': 0,
                'fail': 0,
                'errors': [{'row': 0, 'error': str(exc)}],
            }

        if not rows:
            return {'total': 0, 'success': 0, 'fail': 0, 'errors': []}

        # Validate required columns are present
        first_row_keys = set(rows[0].keys())
        missing = validate_required_columns(first_row_keys)
        if missing:
            return {
                'total': len(rows),
                'success': 0,
                'fail': len(rows),
                'errors': [{'row': 0, 'error': f'Missing columns: {", ".join(sorted(missing))}'}],
            }

        errors_list: list[dict] = []
        success = 0

        for idx, row in enumerate(rows, start=2):
            try:
                self._validate_and_create_question(row, school, user, idx)
                success += 1
            except Exception as exc:
                errors_list.append({'row': idx, 'error': str(exc)})

        return {
            'total': len(rows),
            'success': success,
            'fail': len(errors_list),
            'errors': errors_list,
        }

    def _parse_difficulty_and_marks(self, row: dict) -> tuple[str, Decimal, Decimal]:
        correct_option = row.get('correct_option', '').strip().lower()
        difficulty = row.get('difficulty', '').strip().lower()
        marks_raw = row.get('marks', '').strip()
        negative_marks_raw = row.get('negative_marks', '').strip()

        if correct_option not in ('a', 'b', 'c', 'd'):
            raise ValueError(f'correct_option must be a, b, c, or d — got "{correct_option}".')
        if difficulty not in ('easy', 'medium', 'hard'):
            raise ValueError(f'difficulty must be easy, medium, or hard — got "{difficulty}".')

        try:
            marks = Decimal(str(marks_raw))
        except (InvalidOperation, ValueError):
            raise ValueError(f'marks must be a number — got "{marks_raw}".')

        negative_marks = Decimal('0')
        if negative_marks_raw:
            try:
                negative_marks = Decimal(str(negative_marks_raw))
            except (InvalidOperation, ValueError):
                raise ValueError(f'negative_marks must be a number — got "{negative_marks_raw}".')

        return difficulty, marks, negative_marks

    def _resolve_subject_and_chapter(self, row: dict, school: School) -> tuple[Subject, Chapter]:
        subject_id_raw = row.get('subject_id', '').strip()
        if not subject_id_raw:
            raise ValueError('subject_id (the Subject ID, e.g. KA_MAT_10) is required.')

        subject = Subject.objects.filter(
            school=school, code__iexact=subject_id_raw,
        ).first()

        if subject is None:
            if subject_id_raw.isdigit():
                raise ValueError(
                    f'Subject ID "{subject_id_raw}" is not valid. Use the Subject ID '
                    f'shown on the Subjects page (e.g. KA_MAT_10), not a number.'
                )
            raise ValueError(
                f'Subject ID "{subject_id_raw}" not found in this school. '
                f'Check the Subjects page for the correct ID.'
            )

        chapter_id_raw = row.get('chapter_id', '').strip()
        chapter_name_raw = row.get('chapter_name', '').strip()

        if chapter_id_raw:
            try:
                chapter_id = int(chapter_id_raw)
            except (ValueError, TypeError):
                raise ValueError(
                    f'chapter_id must be an integer — got "{chapter_id_raw}".'
                )
            try:
                chapter = Chapter.objects.get(pk=chapter_id, subject=subject)
            except Chapter.DoesNotExist:
                raise ValueError(
                    f'Chapter with id={chapter_id} does not exist '
                    f'under subject "{subject.name}".'
                )
        elif chapter_name_raw:
            qs = Chapter.objects.filter(
                subject=subject, name__iexact=chapter_name_raw,
            )
            if qs.count() == 0:
                raise ValueError(
                    f'Chapter "{chapter_name_raw}" not found under '
                    f'subject "{subject.name}".'
                )
            if qs.count() > 1:
                raise ValueError(
                    f'Multiple chapters named "{chapter_name_raw}" — '
                    f'please use chapter_id instead.'
                )
            chapter = qs.first()
        else:
            raise ValueError(
                'Either chapter_id or chapter_name must be provided.'
            )

        return subject, chapter

    def _validate_and_create_question(
        self, row: dict, school: School, user, row_num: int,
    ) -> Question:
        """Validate a single row and create a Question object."""
        question_text = row.get('question_text', '').strip()
        option_a = row.get('option_a', '').strip()
        option_b = row.get('option_b', '').strip()
        option_c = row.get('option_c', '').strip()
        option_d = row.get('option_d', '').strip()
        correct_option = row.get('correct_option', '').strip().lower()
        explanation = row.get('explanation', '').strip()

        # Required field checks
        if not question_text:
            raise ValueError('question_text is required.')
        if not all([option_a, option_b, option_c, option_d]):
            raise ValueError('All four options (option_a through option_d) are required.')

        difficulty, marks, negative_marks = self._parse_difficulty_and_marks(row)
        subject, chapter = self._resolve_subject_and_chapter(row, school)

        # Safety net (normally unreachable — both are looked up already scoped: the
        # subject by school, the chapter by that subject).
        if subject.school_id != school.pk:
            raise ValueError(f'Subject "{subject.name}" does not belong to the selected school.')
        if chapter.subject_id != subject.pk:
            raise ValueError(f'Chapter "{chapter.name}" does not belong to subject "{subject.name}".')

        with transaction.atomic():
            question = Question.objects.create(
                school=school,
                subject=subject,
                chapter=chapter,
                created_by=user,
                question_text=question_text,
                option_a=option_a,
                option_b=option_b,
                option_c=option_c,
                option_d=option_d,
                correct_option=correct_option,
                explanation=explanation,
                difficulty=difficulty,
                marks=marks,
                negative_marks=negative_marks,
            )
        return question
