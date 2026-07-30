"""Exam lifecycle state machine — valid chain advances, invalid jumps are blocked."""

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.exams.models import ExamSession
from tests import factories


class TransitionGuardTests(TestCase):
    """Invalid transitions raise before touching the DB (no graph needed)."""

    def test_started_cannot_jump_to_published(self):
        session = ExamSession(status=ExamSession.Status.STARTED)
        with self.assertRaises(ValidationError):
            session.transition_to(ExamSession.Status.PUBLISHED)

    def test_submitted_cannot_go_back_to_started(self):
        session = ExamSession(status=ExamSession.Status.SUBMITTED)
        with self.assertRaises(ValidationError):
            session.transition_to(ExamSession.Status.STARTED)

    def test_abandoned_is_terminal(self):
        session = ExamSession(status=ExamSession.Status.ABANDONED)
        with self.assertRaises(ValidationError):
            session.transition_to(ExamSession.Status.IN_PROGRESS)


class TransitionChainTests(TestCase):
    """The happy-path chain persists each status change."""

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
            status=ExamSession.Status.STARTED,
            time_remaining_seconds=1800,
        )

    def test_started_to_in_progress_to_submitted(self):
        self.session.transition_to(ExamSession.Status.IN_PROGRESS)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ExamSession.Status.IN_PROGRESS)

        self.session.transition_to(ExamSession.Status.SUBMITTED)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ExamSession.Status.SUBMITTED)

    def test_published_can_step_back_to_evaluated(self):
        self.session.status = ExamSession.Status.PUBLISHED
        self.session.save(update_fields=["status"])
        self.session.transition_to(ExamSession.Status.EVALUATED)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ExamSession.Status.EVALUATED)
