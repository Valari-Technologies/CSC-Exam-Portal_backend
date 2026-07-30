"""Serializers for the academic structure: Class, Section, Subject, Chapter.

Cross-FK consistency rules:
- Section.school_class.school must match Section.school.
- Subject.school_class.school must match Subject.school.
- Chapter inherits its school via subject.school_class.school.
- `school` is auto-set on create from the requesting user's school (handled in views).
"""
from rest_framework import serializers

from .models import MAX_GRADE, MIN_GRADE, Chapter, Class, Section, Subject


class ClassSerializer(serializers.ModelSerializer):
    sections_count = serializers.IntegerField(read_only=True)
    subjects_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Class
        fields = (
            'id',
            'school',
            'name',
            'numeric_value',
            'is_active',
            'created_at',
            'sections_count',
            'subjects_count',
        )
        read_only_fields = (
            'id', 'school', 'name', 'created_at', 'sections_count', 'subjects_count',
        )

    def validate_numeric_value(self, value: int) -> int:
        if not MIN_GRADE <= value <= MAX_GRADE:
            raise serializers.ValidationError(
                f'numeric_value must be between {MIN_GRADE} and {MAX_GRADE}.'
            )
        return value

    def validate(self, attrs: dict) -> dict:
        # A class is identified by its grade number; the stored `name` is simply that
        # number as a string (the form no longer collects a free-text name). Derive it
        # here and guard against a duplicate grade within the school with a clean 400
        # (the auto UniqueTogetherValidator can't run once `name` is read-only).
        numeric = attrs.get('numeric_value', getattr(self.instance, 'numeric_value', None))
        if numeric is None:
            return attrs
        attrs['name'] = str(numeric)

        request = self.context.get('request')
        if self.instance is not None:
            school_id = self.instance.school_id
        elif request is not None and request.user.role == 'csc_admin':
            school_id = request.data.get('school')
        elif request is not None:
            school_id = request.user.school_id
        else:
            school_id = None

        if school_id is not None:
            duplicate = Class.objects.filter(school_id=school_id, numeric_value=numeric)
            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError(
                    {'numeric_value': 'A class with this number already exists for this school.'}
                )
        return attrs


class SectionSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(source='school_class.name', read_only=True)
    student_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Section
        fields = (
            'id',
            'school',
            'school_class',
            'class_name',
            'name',
            'is_active',
            'created_at',
            'student_count',
        )
        read_only_fields = ('id', 'school', 'class_name', 'created_at', 'student_count')

    def validate_school_class(self, value: Class) -> Class:
        request = self.context.get('request')
        if request and request.user.role != 'csc_admin' and value.school_id != request.user.school_id:
            raise serializers.ValidationError('Class belongs to a different school.')
        return value


class SubjectSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(source='school_class.name', read_only=True)
    chapter_count = serializers.IntegerField(read_only=True)
    question_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Subject
        fields = (
            'id',
            'school',
            'school_class',
            'class_name',
            'name',
            'code',
            'description',
            'is_active',
            'created_at',
            'chapter_count',
            'question_count',
        )
        read_only_fields = (
            'id',
            'school',
            'class_name',
            # The Subject ID is server-generated (see SubjectViewSet); never client-supplied.
            'code',
            'created_at',
            'chapter_count',
            'question_count',
        )

    def validate_school_class(self, value: Class) -> Class:
        request = self.context.get('request')
        if request and request.user.role != 'csc_admin' and value.school_id != request.user.school_id:
            raise serializers.ValidationError('Class belongs to a different school.')
        return value


class ChapterSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source='subject.name', read_only=True)

    class Meta:
        model = Chapter
        fields = (
            'id',
            'subject',
            'subject_name',
            'name',
            'order_number',
            'description',
            'is_active',
            'created_at',
        )
        read_only_fields = ('id', 'subject_name', 'created_at')

    def validate_subject(self, value: Subject) -> Subject:
        request = self.context.get('request')
        if request and request.user.role != 'csc_admin' and value.school_id != request.user.school_id:
            raise serializers.ValidationError('Subject belongs to a different school.')
        return value

    def validate_order_number(self, value: int) -> int:
        if value < 1:
            raise serializers.ValidationError('order_number must be >= 1.')
        return value
