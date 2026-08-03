"""The assignment window is a hard wall on the whole exam, not just on entry.

Starting outside the window was already refused (StartExamSerializer step 3). The gap
these tests pin down is the other half: a session that is legitimately IN_PROGRESS must
also close at `end_datetime`, even when the test's own duration still has time left.
Without that bound a student could enter with one minute of the window remaining and
keep working for the full duration past it.

Two deadlines therefore apply and the EARLIER wins:

1. `test.duration_minutes`, counted from `started_at`.
2. `assignment.end_datetime`.

Every mutating endpoint routes through `_sync_server_timer`, so each is covered here —
a fix that only guarded `submit` would leave `save-answer` open.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.exams.models import ExamSession
from apps.results.models import Result

from .factories import (
    make_assignment,
    make_chapter,
    make_class,
    make_question,
    make_school,
    make_section,
    make_student,
    make_subject,
    make_test,
    make_user,
)


class ExamWindowTestCase(APITestCase):
    """Shared object graph: one published test, assigned to one class."""

    # Long enough that the duration can never be what ends the exam — only the
    # window can. Tests that want the opposite shorten it explicitly.
    DURATION_MINUTES = 120

    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')
        self.subject = make_subject(self.school, self.klass)
        self.chapter = make_chapter(self.subject)
        self.teacher = make_user('teacher', self.school)
        self.student = make_student(self.school, self.klass, self.section)

        self.question = make_question(
            self.school, self.subject, self.chapter,
            correct_option='a', marks=Decimal('2'),
        )
        self.test = make_test(
            self.school, self.subject, self.klass, self.teacher,
            questions=[self.question],
            duration_minutes=self.DURATION_MINUTES,
        )
        self.assignment = make_assignment(self.test, self.klass, self.teacher)
        self.client.force_authenticate(self.student)

    def _set_window(self, *, starts_in: timedelta, ends_in: timedelta) -> None:
        """Move the assignment window relative to now (negative = in the past)."""
        now = timezone.now()
        self.assignment.start_datetime = now + starts_in
        self.assignment.end_datetime = now + ends_in
        self.assignment.save(update_fields=['start_datetime', 'end_datetime'])

    def _start(self):
        return self.client.post(
            '/api/v1/exam/sessions/start/', {'assignment_id': self.assignment.pk},
        )

    def _start_ok(self) -> int:
        response = self._start()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response.data['id']

    def _expire_window(self) -> None:
        """Close the window in the past, leaving the session's own duration intact.

        Rewriting the assignment rather than the session is what isolates the new
        rule: `started_at` is untouched, so the duration-based timer still has
        plenty of time and only the window can end the exam.
        """
        self.assignment.end_datetime = timezone.now() - timedelta(minutes=1)
        self.assignment.save(update_fields=['end_datetime'])


class StartBoundaryTests(ExamWindowTestCase):
    """Entry is refused outside the window (existing rule — guarded against regression)."""

    def test_cannot_start_before_window_opens(self):
        self._set_window(starts_in=timedelta(hours=1), ends_in=timedelta(hours=2))
        response = self._start()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('not started', str(response.data).lower())

    def test_cannot_start_after_window_closes(self):
        self._set_window(starts_in=timedelta(hours=-2), ends_in=timedelta(hours=-1))
        response = self._start()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('closed', str(response.data).lower())

    def test_no_session_is_created_when_the_window_has_closed(self):
        """A refused start must leave nothing behind to resume into."""
        self._set_window(starts_in=timedelta(hours=-2), ends_in=timedelta(hours=-1))
        self._start()
        self.assertFalse(ExamSession.objects.filter(student=self.student).exists())

    def test_can_start_inside_the_window(self):
        self._set_window(starts_in=timedelta(minutes=-5), ends_in=timedelta(hours=1))
        session_id = self._start_ok()
        self.assertEqual(
            ExamSession.objects.get(pk=session_id).status,
            ExamSession.Status.IN_PROGRESS,
        )


class StartTimerIsBoundedByWindowTests(ExamWindowTestCase):
    """A late entrant gets the full duration, not limited by the window."""

    def test_late_start_gets_full_duration(self):
        # 10 minutes of window left, but a 120-minute test.
        self._set_window(starts_in=timedelta(hours=-1), ends_in=timedelta(minutes=10))
        response = self._start()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        remaining = response.data['time_remaining_seconds']
        self.assertEqual(remaining, self.DURATION_MINUTES * 60)

    def test_early_start_gets_the_full_duration(self):
        self._set_window(starts_in=timedelta(minutes=-1), ends_in=timedelta(hours=5))
        response = self._start()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(
            response.data['time_remaining_seconds'], self.DURATION_MINUTES * 60,
        )


class InFlightSessionStaysOpenAfterWindowEndTests(ExamWindowTestCase):
    """An already-running session stays in progress even after the window ends."""

    def test_retrieve_does_not_auto_submit_once_the_window_has_closed(self):
        session_id = self._start_ok()
        self._expire_window()

        response = self.client.get(f'/api/v1/exam/sessions/{session_id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], ExamSession.Status.IN_PROGRESS)

    def test_save_answer_is_allowed_once_the_window_has_closed(self):
        session_id = self._start_ok()
        self._expire_window()

        response = self.client.post(
            f'/api/v1/exam/sessions/{session_id}/save-answer/',
            {'question_id': self.question.pk, 'selected_option': 'a'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_answer_sent_after_the_window_closed_is_recorded(self):
        session_id = self._start_ok()
        self._expire_window()

        self.client.post(
            f'/api/v1/exam/sessions/{session_id}/save-answer/',
            {'question_id': self.question.pk, 'selected_option': 'a'},
        )
        # Manually submit to verify recorded answer
        self.client.post(f'/api/v1/exam/sessions/{session_id}/submit/')
        detail = Result.objects.get(session_id=session_id).details.get(
            question=self.question,
        )
        self.assertEqual(detail.selected_option, 'a')

    def test_cheat_event_is_allowed_once_the_window_has_closed(self):
        session_id = self._start_ok()
        self._expire_window()

        response = self.client.post(
            f'/api/v1/exam/sessions/{session_id}/cheat-event/',
            {'event_type': 'tab_switch'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_submit_after_the_window_closed_still_finalises_the_attempt(self):
        session_id = self._start_ok()
        self._expire_window()

        response = self.client.post(f'/api/v1/exam/sessions/{session_id}/submit/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        session = ExamSession.objects.get(pk=session_id)
        self.assertEqual(session.status, ExamSession.Status.SUBMITTED)
        self.assertTrue(Result.objects.filter(session=session).exists())

    def test_an_open_window_leaves_the_session_running(self):
        session_id = self._start_ok()

        response = self.client.get(f'/api/v1/exam/sessions/{session_id}/')
        self.assertEqual(response.data['status'], ExamSession.Status.IN_PROGRESS)
        self.assertGreater(response.data['time_remaining_seconds'], 0)


class DurationStillEndsTheExamTests(ExamWindowTestCase):
    """The window bound is an ADDITION — the duration deadline must survive it."""

    def test_duration_expiry_auto_submits_inside_a_generous_window(self):
        self._set_window(starts_in=timedelta(minutes=-1), ends_in=timedelta(days=1))
        session_id = self._start_ok()

        # Backdate the start so the 120-minute duration has run out, while the
        # window stays wide open for another day.
        session = ExamSession.objects.get(pk=session_id)
        session.started_at = timezone.now() - timedelta(minutes=self.DURATION_MINUTES + 1)
        session.save(update_fields=['started_at'])

        response = self.client.get(f'/api/v1/exam/sessions/{session_id}/')
        self.assertEqual(response.data['status'], ExamSession.Status.SUBMITTED)


class AssignmentStaysVisibleToTheStudentTests(ExamWindowTestCase):
    """An expired assignment must still be LISTED, not filtered away.

    The student page derives its button label — Upcoming / Start Exam / Expired —
    from `start_datetime` and `end_datetime` on each row it receives. That only
    works while the row keeps arriving: an assignment dropped from the list once
    its window closed would render as nothing at all rather than as "Expired",
    which is the opposite of "it must remain in the Expired state".

    `is_active` is a stored flag an author toggles by hand, NOT a derived one —
    nothing clears it when the window passes. These tests exist so that stays
    true: adding a time filter to the student queryset (or auto-clearing the
    flag on expiry) would look like tidying and would silently break the label.
    """

    LIST_URL = '/api/v1/tests/assignments/'

    def _listed_ids(self) -> set[int]:
        """Exactly the call the student page makes, filter included."""
        response = self.client.get(self.LIST_URL, {'is_active': 'true'})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return {row['id'] for row in response.data['results']}

    def test_expired_assignment_is_still_listed(self):
        self._set_window(starts_in=timedelta(days=-2), ends_in=timedelta(days=-1))
        self.assertIn(self.assignment.pk, self._listed_ids())

    def test_upcoming_assignment_is_still_listed(self):
        self._set_window(starts_in=timedelta(days=1), ends_in=timedelta(days=2))
        self.assertIn(self.assignment.pk, self._listed_ids())

    def test_open_assignment_is_listed(self):
        """The control — the case that was never in doubt."""
        self._set_window(starts_in=timedelta(minutes=-1), ends_in=timedelta(hours=1))
        self.assertIn(self.assignment.pk, self._listed_ids())

    def test_expiry_does_not_clear_the_is_active_flag(self):
        """`is_active` means "the author switched this off", never "time passed"."""
        self._set_window(starts_in=timedelta(days=-2), ends_in=timedelta(days=-1))
        self.client.get(self.LIST_URL, {'is_active': 'true'})
        self.assignment.refresh_from_db()
        self.assertTrue(self.assignment.is_active)

    def test_the_window_is_what_the_label_reads(self):
        """The row carries both bounds, so the client can place `now` between them."""
        self._set_window(starts_in=timedelta(days=-2), ends_in=timedelta(days=-1))
        response = self.client.get(self.LIST_URL, {'is_active': 'true'})
        row = next(r for r in response.data['results'] if r['id'] == self.assignment.pk)
        self.assertIsNotNone(row['start_datetime'])
        self.assertIsNotNone(row['end_datetime'])
        self.assertLess(row['end_datetime'], timezone.now().isoformat())

    def test_an_author_deactivated_assignment_is_hidden(self):
        """The flag still does its own job — the filter is not decorative."""
        self.assignment.is_active = False
        self.assignment.save(update_fields=['is_active'])
        self.assertNotIn(self.assignment.pk, self._listed_ids())


class ServerSideAvailabilityTests(ExamWindowTestCase):
    """The window state is reported by the server, not left to the device clock.

    The student page still ticks its own clock so a card transitions while it sits
    open — but a device set an hour slow would otherwise draw "Start Exam" on a
    window that closed an hour ago. The start endpoint always refused such an
    attempt, so this was never a way in; it was the button telling the student
    something the server would not honour. `availability` is what the two now
    agree on.
    """

    LIST_URL = '/api/v1/tests/assignments/'

    def _availability(self) -> str:
        response = self.client.get(self.LIST_URL, {'is_active': 'true'})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = next(r for r in response.data['results'] if r['id'] == self.assignment.pk)
        return row['availability']

    def test_before_the_window_reads_upcoming(self):
        self._set_window(starts_in=timedelta(hours=1), ends_in=timedelta(hours=2))
        self.assertEqual(self._availability(), 'upcoming')

    def test_inside_the_window_reads_open(self):
        self._set_window(starts_in=timedelta(minutes=-1), ends_in=timedelta(hours=1))
        self.assertEqual(self._availability(), 'open')

    def test_after_the_window_reads_expired(self):
        self._set_window(starts_in=timedelta(days=-2), ends_in=timedelta(days=-1))
        self.assertEqual(self._availability(), 'expired')

    def test_expired_stays_expired(self):
        """There is no path back: the state is derived from an immutable window."""
        self._set_window(starts_in=timedelta(days=-2), ends_in=timedelta(days=-1))
        self.assertEqual(self._availability(), 'expired')
        self.assertEqual(self._availability(), 'expired')

    def test_availability_agrees_with_what_start_allows(self):
        """The label and the gate must never disagree — that was the whole bug."""
        for starts_in, ends_in, expected, startable in (
            (timedelta(hours=1), timedelta(hours=2), 'upcoming', False),
            (timedelta(minutes=-1), timedelta(hours=1), 'open', True),
            (timedelta(days=-2), timedelta(days=-1), 'expired', False),
        ):
            with self.subTest(expected=expected):
                self._set_window(starts_in=starts_in, ends_in=ends_in)
                self.assertEqual(self._availability(), expected)

                ExamSession.objects.filter(student=self.student).delete()
                response = self._start()
                if startable:
                    self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                else:
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
