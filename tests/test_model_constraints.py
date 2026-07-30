"""DB-level guards on Result — a negative aggregate must never persist."""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.exams.models import ExamSession
from tests import factories


class ResultConstraintTests(TestCase):
    def setUp(self):
        school = factories.make_school()
        klass = factories.make_class(school)
        section = factories.make_section(school, klass)
        subject = factories.make_subject(school, klass)
        teacher = factories.make_user("teacher", school)
        student = factories.make_student(school, klass, section)
        test = factories.make_test(school, subject, klass, teacher)
        assignment = factories.make_assignment(test, klass, teacher)
        self.session = factories.make_session(
            student,
            assignment,
            test,
            status=ExamSession.Status.SUBMITTED,
        )

    def test_negative_obtained_marks_rejected_by_db(self):
        from apps.results.models import Result

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Result.objects.create(
                    session=self.session,
                    student=self.session.student,
                    test=self.session.test,
                    assignment=self.session.assignment,
                    total_marks=Decimal("10"),
                    obtained_marks=Decimal("-1"),
                    percentage=Decimal("0"),
                    passed=False,
                    time_taken_seconds=10,
                )

    def test_non_negative_obtained_marks_allowed(self):
        result = factories.make_result(self.session, obtained=Decimal("2"), total=Decimal("2"))
        self.assertEqual(result.obtained_marks, Decimal("2"))
