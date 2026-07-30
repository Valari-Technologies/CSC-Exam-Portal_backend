"""Plain serializers for report response shapes — no model backing."""
from rest_framework import serializers


class ClassReportSerializer(serializers.Serializer):
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    total_students = serializers.IntegerField()
    avg_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    pass_count = serializers.IntegerField()
    fail_count = serializers.IntegerField()


class SubjectReportSerializer(serializers.Serializer):
    subject_id = serializers.IntegerField()
    subject_name = serializers.CharField()
    avg_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    question_count = serializers.IntegerField()
    test_count = serializers.IntegerField()


class TestReportSerializer(serializers.Serializer):
    test_id = serializers.IntegerField()
    test_title = serializers.CharField()
    total_students = serializers.IntegerField()
    avg_marks = serializers.DecimalField(max_digits=7, decimal_places=2, allow_null=True)
    highest_marks = serializers.DecimalField(max_digits=7, decimal_places=2, allow_null=True)
    lowest_marks = serializers.DecimalField(max_digits=7, decimal_places=2, allow_null=True)
    avg_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    pass_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)


class StudentReportSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    student_email = serializers.EmailField()
    total_tests = serializers.IntegerField()
    avg_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    total_passed = serializers.IntegerField()
    total_failed = serializers.IntegerField()
    results = serializers.ListField(child=serializers.DictField(), required=False)
