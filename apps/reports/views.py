"""Reports API — aggregated analytics endpoints, all school-scoped.

Permission model
- CSC Admin: all schools (optional ?school_id= filter).
- School Admin / Teacher: own school only.
- Student: own performance history only (student/<id>/ where id == self).
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Max, Min, Q
from django.utils.dateparse import parse_date
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import Class, Subject
from apps.questions.models import Question
from apps.results.models import Result
from apps.tests.models import Test

from .serializers import (
    ClassReportSerializer,
    StudentReportSerializer,
    SubjectReportSerializer,
    TestReportSerializer,
)

User = get_user_model()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_school_id(request) -> int | None:
    """Return the school_id to scope queries to, or None for CSC Admin unscoped."""
    user = request.user
    if user.role == 'csc_admin':
        school_id = request.query_params.get('school_id')
        return int(school_id) if school_id else None
    if user.role in ('school_admin', 'teacher'):
        if user.school_id:
            return user.school_id
        raise PermissionDenied('Cannot determine school scope.')
    # Student role handled per-endpoint
    return None


# ---------------------------------------------------------------------------
# Class-wise report
# ---------------------------------------------------------------------------

class ClassReportView(APIView):
    """GET /reports/class/ — class-wise performance aggregated from Results.

    Optional query params:
    - school_id: filter by school (CSC Admin only, already handled by helper)
    - date_from: filter results with calculated_at >= date (YYYY-MM-DD)
    - date_to: filter results with calculated_at <= date (YYYY-MM-DD)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role == 'student':
            raise PermissionDenied('Students cannot access class reports.')

        school_id = _get_school_id(request)

        # Filter results by school
        result_qs = Result.objects.all()
        if school_id:
            result_qs = result_qs.filter(test__school_id=school_id)

        # Date range filters
        date_from_str: str | None = request.query_params.get('date_from')
        date_to_str: str | None = request.query_params.get('date_to')

        if date_from_str:
            date_from = parse_date(date_from_str)
            if date_from is None:
                return Response(
                    {'detail': 'Invalid date_from format. Use YYYY-MM-DD.'},
                    status=400,
                )
            result_qs = result_qs.filter(calculated_at__date__gte=date_from)

        if date_to_str:
            date_to = parse_date(date_to_str)
            if date_to is None:
                return Response(
                    {'detail': 'Invalid date_to format. Use YYYY-MM-DD.'},
                    status=400,
                )
            result_qs = result_qs.filter(calculated_at__date__lte=date_to)

        class_qs = Class.objects.all()
        if school_id:
            class_qs = class_qs.filter(school_id=school_id)

        data = []
        for cls in class_qs.filter(is_active=True).order_by('numeric_value'):
            # Results for tests in this class
            class_results = result_qs.filter(test__school_class=cls)
            agg = class_results.aggregate(
                total_students=Count('student', distinct=True),
                avg_percentage=Avg('percentage'),
                pass_count=Count('id', filter=Q(passed=True)),
                fail_count=Count('id', filter=Q(passed=False)),
            )
            data.append({
                'class_id': cls.id,
                'class_name': cls.name,
                'total_students': agg['total_students'] or 0,
                'avg_percentage': agg['avg_percentage'],
                'pass_count': agg['pass_count'] or 0,
                'fail_count': agg['fail_count'] or 0,
            })

        serializer = ClassReportSerializer(data, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Subject-wise report
# ---------------------------------------------------------------------------

class SubjectReportView(APIView):
    """GET /reports/subject/ — subject-wise performance.

    Optional query params:
    - school_id: filter by school (CSC Admin only, already handled by helper)
    - school_class: filter subjects, results, and tests by class ID
    """
    permission_classes = [IsAuthenticated]

    def _get_subject_stats(self, subject, result_qs, school_id):
        """Return a dict of aggregated stats for a single subject."""
        subject_results = result_qs.filter(test__subject=subject)
        agg = subject_results.aggregate(avg_percentage=Avg('percentage'))

        question_qs = Question.objects.filter(subject=subject, is_active=True)
        if school_id:
            question_qs = question_qs.filter(school_id=school_id)

        test_qs = Test.objects.filter(subject=subject)
        if school_id:
            test_qs = test_qs.filter(school_id=school_id)

        return {
            'subject_id': subject.id,
            'subject_name': subject.name,
            'avg_percentage': agg['avg_percentage'],
            'question_count': question_qs.count(),
            'test_count': test_qs.count(),
        }

    def get(self, request):
        if request.user.role == 'student':
            raise PermissionDenied('Students cannot access subject reports.')

        school_id = _get_school_id(request)
        school_class_id = self._parse_school_class(request)

        subject_qs = Subject.objects.filter(is_active=True)
        if school_id:
            subject_qs = subject_qs.filter(school_id=school_id)
        if school_class_id:
            subject_qs = subject_qs.filter(school_class_id=school_class_id)

        result_qs = Result.objects.all()
        if school_id:
            result_qs = result_qs.filter(test__school_id=school_id)
        if school_class_id:
            result_qs = result_qs.filter(test__school_class_id=school_class_id)

        data = [
            self._get_subject_stats(subject, result_qs, school_id)
            for subject in subject_qs.order_by('name')
        ]

        serializer = SubjectReportSerializer(data, many=True)
        return Response(serializer.data)

    def _parse_school_class(self, request):
        """Parse and validate the school_class query param. Returns int or None."""
        param = request.query_params.get('school_class')
        if not param:
            return None
        try:
            return int(param)
        except (ValueError, TypeError):
            raise ValidationError({'detail': 'Invalid school_class parameter.'})


# ---------------------------------------------------------------------------
# Per-test report
# ---------------------------------------------------------------------------

class TestReportView(APIView):
    """GET /reports/test/<id>/ — per-test analysis."""
    permission_classes = [IsAuthenticated]

    def get(self, request, test_id: int):
        if request.user.role == 'student':
            raise PermissionDenied('Students cannot access test reports.')

        try:
            test = Test.objects.get(pk=test_id)
        except Test.DoesNotExist:
            raise NotFound('Test not found.')

        # School scope check
        if request.user.role != 'csc_admin':
            if request.user.school_id != test.school_id:
                raise PermissionDenied('You do not have access to this test.')

        results = Result.objects.filter(test=test)

        agg = results.aggregate(
            total_students=Count('id'),
            avg_marks=Avg('obtained_marks'),
            highest_marks=Max('obtained_marks'),
            lowest_marks=Min('obtained_marks'),
            avg_percentage=Avg('percentage'),
            pass_count=Count('id', filter=Q(passed=True)),
        )

        total = agg['total_students'] or 0
        pass_count = agg['pass_count'] or 0
        pass_pct = round(pass_count / total * 100, 2) if total > 0 else None

        data = {
            'test_id': test.id,
            'test_title': test.title,
            'total_students': total,
            'avg_marks': agg['avg_marks'],
            'highest_marks': agg['highest_marks'],
            'lowest_marks': agg['lowest_marks'],
            'avg_percentage': agg['avg_percentage'],
            'pass_percentage': pass_pct,
        }

        serializer = TestReportSerializer(data)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Per-student report
# ---------------------------------------------------------------------------

class StudentReportView(APIView):
    """GET /reports/student/<id>/ — per-student performance history."""
    permission_classes = [IsAuthenticated]

    def get(self, request, student_id: int):
        # Students can only see their own report
        if request.user.role == 'student' and request.user.id != student_id:
            raise PermissionDenied('You can only view your own report.')

        try:
            student = User.objects.get(pk=student_id, role='student')
        except User.DoesNotExist:
            raise NotFound('Student not found.')

        # School scope check for teacher/school_admin
        if request.user.role in ('teacher', 'school_admin'):
            if request.user.school_id != student.school_id:
                raise PermissionDenied('You do not have access to this student.')

        result_qs = Result.objects.filter(student=student)

        # For students, only show published results
        if request.user.role == 'student':
            result_qs = result_qs.filter(is_published=True)

        # Optional subject filter
        subject_param = request.query_params.get('subject')
        if subject_param:
            try:
                subject_id = int(subject_param)
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Invalid subject parameter.'},
                    status=400,
                )
            result_qs = result_qs.filter(test__subject_id=subject_id)

        agg = result_qs.aggregate(
            total_tests=Count('id'),
            avg_percentage=Avg('percentage'),
            total_passed=Count('id', filter=Q(passed=True)),
            total_failed=Count('id', filter=Q(passed=False)),
        )

        # Individual result entries
        results_list = list(
            result_qs
            .select_related('test')
            .order_by('-calculated_at')
            .values(
                'id',
                'test__title',
                'obtained_marks',
                'total_marks',
                'percentage',
                'passed',
                'rank',
                'calculated_at',
            )
        )
        # Rename test__title for frontend
        for r in results_list:
            r['test_title'] = r.pop('test__title')

        data = {
            'student_id': student.id,
            'student_name': student.full_name,
            'student_email': student.email,
            'total_tests': agg['total_tests'] or 0,
            'avg_percentage': agg['avg_percentage'],
            'total_passed': agg['total_passed'] or 0,
            'total_failed': agg['total_failed'] or 0,
            'results': results_list,
        }

        serializer = StudentReportSerializer(data)
        return Response(serializer.data)
