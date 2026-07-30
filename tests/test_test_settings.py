"""Test Management: mandatory fields, and the four test settings actually taking effect.

The four settings (shuffle_questions, shuffle_options, show_result_immediately,
allow_review_after_submit) were stored on the model and round-tripped through the API
since day one, but NOTHING read them — every exam ran unshuffled, every result waited for
a teacher to publish, and every student could review. Implemented 2026-07-20.

The invariant these tests exist to protect: shuffling is a DISPLAY concern only. Options
are reordered but each keeps its identity letter, so `selected_option` means exactly what
it always meant and grading is untouched. `test_shuffled_options_still_grade_correctly`
is the one that must never go red.
"""
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.exams.models import ExamAnswer, ExamSession
from apps.results.models import Result
from apps.tests.models import Test, TestAssignment

from . import factories

CANONICAL = ['a', 'b', 'c', 'd']


class _TestModuleBase(APITestCase):
    def setUp(self):
        self.school = factories.make_school()
        self.klass = factories.make_class(self.school)
        self.section = factories.make_section(self.school, self.klass)
        self.subject = factories.make_subject(self.school, self.klass)
        self.chapter = factories.make_chapter(self.subject)
        self.teacher = factories.make_user('teacher', self.school)
        self.student = factories.make_student(self.school, self.klass, self.section)

    def _questions(self, count: int, correct: str = 'a'):
        return [
            factories.make_question(
                self.school, self.subject, self.chapter, correct_option=correct,
            )
            for _ in range(count)
        ]

    def _test_with(self, questions, **settings) -> Test:
        test = factories.make_test(
            self.school, self.subject, self.klass, self.teacher, questions=questions,
        )
        if settings:
            for field, value in settings.items():
                setattr(test, field, value)
            test.save(update_fields=list(settings))
        return test

    def _start(self, test) -> dict:
        """Start an exam as the student and return the session payload."""
        assignment = factories.make_assignment(test, self.klass, self.teacher)
        self.client.force_authenticate(self.student)
        resp = self.client.post('/api/v1/exam/sessions/start/', {'assignment_id': assignment.pk})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return resp.data

    def _retrieve(self, session_id: int) -> dict:
        resp = self.client.get(f'/api/v1/exam/sessions/{session_id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return resp.data


# ---------------------------------------------------------------------------
# 1. Mandatory fields on test creation
# ---------------------------------------------------------------------------

class TestCreationRequiredFieldTests(_TestModuleBase):
    """Class, Subject, Duration, Total Marks and Passing Marks are all required.

    total_marks/passing_marks carry a model default of 0, which made DRF treat them as
    optional — a POST omitting them used to 201 and create a test worth nothing.
    """

    def _payload(self, **overrides) -> dict:
        payload = {
            'title': 'Unit Test 1',
            'subject': self.subject.pk,
            'school_class': self.klass.pk,
            'total_marks': 10,
            'passing_marks': 4,
            'duration_minutes': 30,
        }
        payload.update(overrides)
        return payload

    def _post(self, payload):
        self.client.force_authenticate(self.teacher)
        return self.client.post('/api/v1/tests/', payload)

    def test_complete_payload_creates_the_test(self):
        resp = self._post(self._payload())

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_missing_total_marks_is_rejected(self):
        payload = self._payload()
        del payload['total_marks']

        resp = self._post(payload)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('total_marks', resp.data)

    def test_missing_passing_marks_is_rejected(self):
        payload = self._payload()
        del payload['passing_marks']

        resp = self._post(payload)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('passing_marks', resp.data)

    def test_missing_duration_is_rejected(self):
        payload = self._payload()
        del payload['duration_minutes']

        resp = self._post(payload)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('duration_minutes', resp.data)

    def test_missing_class_and_subject_are_rejected(self):
        for field in ('school_class', 'subject'):
            payload = self._payload()
            del payload[field]

            resp = self._post(payload)

            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(field, resp.data)

    def test_instructions_stay_optional(self):
        """Only the five named fields became mandatory — Instructions did not."""
        payload = self._payload()
        payload.pop('instructions', None)

        resp = self._post(payload)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data.get('instructions', ''), '')

    def test_zero_total_marks_is_rejected(self):
        """The blank-field case. A cleared number input coerces to 0 on the way through,
        so "present" is not a strong enough rule for total_marks — it must be > 0."""
        resp = self._post(self._payload(total_marks=0))

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('total_marks', resp.data)

    def test_zero_passing_marks_is_allowed(self):
        """Unlike total, 0 here is a real choice — it means everyone passes."""
        resp = self._post(self._payload(total_marks=10, passing_marks=0))

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_passing_marks_may_not_exceed_total(self):
        resp = self._post(self._payload(total_marks=10, passing_marks=11))

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('passing_marks', resp.data)

    def test_partial_update_does_not_demand_every_field(self):
        """PATCH must stay usable — required=True applies to create/PUT, not partial."""
        test = self._test_with(self._questions(1))
        self.client.force_authenticate(self.teacher)

        resp = self.client.patch(f'/api/v1/tests/{test.pk}/', {'title': 'Renamed'})

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)


# ---------------------------------------------------------------------------
# 2a. shuffle_questions
# ---------------------------------------------------------------------------

class ShuffleQuestionsTests(_TestModuleBase):
    def test_off_delivers_questions_in_test_order(self):
        questions = self._questions(6)
        test = self._test_with(questions, shuffle_questions=False)

        session = self._start(test)

        self.assertEqual(
            [q['id'] for q in session['questions']], [q.pk for q in questions],
        )

    def test_on_keeps_the_same_questions(self):
        questions = self._questions(6)
        test = self._test_with(questions, shuffle_questions=True)

        session = self._start(test)

        self.assertEqual(
            sorted(q['id'] for q in session['questions']),
            sorted(q.pk for q in questions),
        )

    def test_on_is_stable_across_refreshes(self):
        """A student who reloads mid-exam must not get a reshuffled paper."""
        test = self._test_with(self._questions(8), shuffle_questions=True)

        started = self._start(test)
        first = [q['id'] for q in started['questions']]
        second = [q['id'] for q in self._retrieve(started['id'])['questions']]
        third = [q['id'] for q in self._retrieve(started['id'])['questions']]

        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_on_actually_varies_between_sessions(self):
        """Seeded per session, so different students get different orders."""
        questions = self._questions(8)
        canonical = [q.pk for q in questions]
        test = self._test_with(questions, shuffle_questions=True)

        orders = []
        for _ in range(6):
            other = factories.make_student(self.school, self.klass, self.section)
            assignment = factories.make_assignment(test, self.klass, self.teacher)
            self.client.force_authenticate(other)
            resp = self.client.post(
                '/api/v1/exam/sessions/start/', {'assignment_id': assignment.pk},
            )
            orders.append([q['id'] for q in resp.data['questions']])

        # With 8 questions the odds of all six matching the canonical order by chance are
        # about 1 in 40320^6 — a failure here means shuffling is not happening.
        self.assertTrue(any(order != canonical for order in orders))


# ---------------------------------------------------------------------------
# 2b. shuffle_options — display only, grading must not move
# ---------------------------------------------------------------------------

class ShuffleOptionsTests(_TestModuleBase):
    def test_off_delivers_canonical_option_order(self):
        test = self._test_with(self._questions(3), shuffle_options=False)

        session = self._start(test)

        for question in session['questions']:
            self.assertEqual(question['option_order'], CANONICAL)

    def test_on_delivers_a_permutation_not_a_subset(self):
        test = self._test_with(self._questions(5), shuffle_options=True)

        session = self._start(test)

        for question in session['questions']:
            self.assertEqual(sorted(question['option_order']), CANONICAL)

    def test_on_is_stable_across_refreshes(self):
        test = self._test_with(self._questions(5), shuffle_options=True)

        started = self._start(test)
        first = {q['id']: q['option_order'] for q in started['questions']}
        second = {q['id']: q['option_order'] for q in self._retrieve(started['id'])['questions']}

        self.assertEqual(first, second)

    def test_on_actually_reorders_something(self):
        test = self._test_with(self._questions(10), shuffle_options=True)

        session = self._start(test)

        orders = [q['option_order'] for q in session['questions']]
        self.assertTrue(any(order != CANONICAL for order in orders))

    def test_option_text_is_not_moved_between_letters(self):
        """option_a is still option_a — only the order to display them in changes."""
        questions = self._questions(3)
        test = self._test_with(questions, shuffle_options=True)

        session = self._start(test)

        by_id = {q.pk: q for q in questions}
        for payload in session['questions']:
            source = by_id[payload['id']]
            self.assertEqual(payload['option_a'], source.option_a)
            self.assertEqual(payload['option_b'], source.option_b)
            self.assertEqual(payload['option_c'], source.option_c)
            self.assertEqual(payload['option_d'], source.option_d)

    def test_shuffled_options_still_grade_correctly(self):
        """THE invariant: answering with the correct letter scores, shuffle or not.

        Grading compares stored `selected_option` to `correct_option`. Because shuffling
        only reorders display and never relabels an option, that comparison is unaffected.
        """
        questions = self._questions(4, correct='c')
        test = self._test_with(
            questions, shuffle_options=True, shuffle_questions=True,
        )

        started = self._start(test)
        session_id = started['id']
        for question in started['questions']:
            resp = self.client.post(
                f'/api/v1/exam/sessions/{session_id}/save-answer/',
                {'question_id': question['id'], 'selected_option': 'c'},
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        submit = self.client.post(f'/api/v1/exam/sessions/{session_id}/submit/')

        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.data)
        result = Result.objects.get(session_id=session_id)
        self.assertEqual(result.correct_count, 4)
        self.assertEqual(result.wrong_count, 0)
        self.assertEqual(result.obtained_marks, result.total_marks)

    def test_a_wrong_answer_is_still_wrong_when_shuffled(self):
        """The mirror of the above — shuffling must not turn errors into marks."""
        questions = self._questions(3, correct='c')
        test = self._test_with(questions, shuffle_options=True)

        started = self._start(test)
        session_id = started['id']
        for question in started['questions']:
            self.client.post(
                f'/api/v1/exam/sessions/{session_id}/save-answer/',
                {'question_id': question['id'], 'selected_option': 'b'},
            )

        self.client.post(f'/api/v1/exam/sessions/{session_id}/submit/')

        result = Result.objects.get(session_id=session_id)
        self.assertEqual(result.correct_count, 0)
        self.assertEqual(result.wrong_count, 3)


# ---------------------------------------------------------------------------
# 2c. show_result_immediately
# ---------------------------------------------------------------------------

class ShowResultImmediatelyTests(_TestModuleBase):
    def _submit(self, test) -> int:
        started = self._start(test)
        self.client.post(f'/api/v1/exam/sessions/{started["id"]}/submit/')
        return started['id']

    def test_off_leaves_the_result_unpublished(self):
        test = self._test_with(self._questions(2), show_result_immediately=False)

        session_id = self._submit(test)

        result = Result.objects.get(session_id=session_id)
        self.assertFalse(result.is_published)
        self.assertIsNone(result.published_at)

    def test_on_publishes_the_result_at_submission(self):
        test = self._test_with(self._questions(2), show_result_immediately=True)

        session_id = self._submit(test)

        result = Result.objects.get(session_id=session_id)
        self.assertTrue(result.is_published)
        self.assertIsNotNone(result.published_at)

    def test_on_makes_the_result_visible_to_the_student_without_a_teacher(self):
        test = self._test_with(self._questions(2), show_result_immediately=True)
        session_id = self._submit(test)
        result = Result.objects.get(session_id=session_id)

        resp = self.client.get(f'/api/v1/results/{result.pk}/')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_teacher_evaluation_does_not_unpublish_an_immediate_result(self):
        """New state: a published Result on a still-SUBMITTED session. Evaluating must not
        yank a result the student has already been shown."""
        test = self._test_with(self._questions(2), show_result_immediately=True)
        session_id = self._submit(test)
        self.client.force_authenticate(self.teacher)

        resp = self.client.post(f'/api/v1/exam/sessions/{session_id}/evaluate/', {}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(Result.objects.get(session_id=session_id).is_published)

    def test_off_keeps_the_result_hidden_from_the_student(self):
        test = self._test_with(self._questions(2), show_result_immediately=False)
        session_id = self._submit(test)
        result = Result.objects.get(session_id=session_id)

        resp = self.client.get(f'/api/v1/results/{result.pk}/')

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# 2d. allow_review_after_submit
# ---------------------------------------------------------------------------

class AllowReviewAfterSubmitTests(_TestModuleBase):
    def _published_result(self, allow_review: bool) -> Result:
        test = self._test_with(
            self._questions(3),
            show_result_immediately=True,
            allow_review_after_submit=allow_review,
        )
        started = self._start(test)
        self.client.post(f'/api/v1/exam/sessions/{started["id"]}/submit/')
        return Result.objects.get(session_id=started['id'])

    def test_on_gives_the_student_the_per_question_breakdown(self):
        result = self._published_result(allow_review=True)

        resp = self.client.get(f'/api/v1/results/{result.pk}/')

        self.assertTrue(resp.data['review_allowed'])
        self.assertEqual(len(resp.data['details']), 3)

    def test_off_withholds_the_breakdown_but_keeps_the_score(self):
        result = self._published_result(allow_review=False)

        resp = self.client.get(f'/api/v1/results/{result.pk}/')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['review_allowed'])
        self.assertEqual(resp.data['details'], [])
        # The score itself is not part of the review — it stays visible.
        self.assertIn('obtained_marks', resp.data)
        self.assertEqual(resp.data['correct_count'], result.correct_count)

    def test_off_does_not_blind_the_teacher(self):
        """The setting governs what the STUDENT may review, not what staff can evaluate."""
        result = self._published_result(allow_review=False)
        self.client.force_authenticate(self.teacher)

        resp = self.client.get(f'/api/v1/results/{result.pk}/')

        self.assertTrue(resp.data['review_allowed'])
        self.assertEqual(len(resp.data['details']), 3)


# ---------------------------------------------------------------------------
# 3 & 5. Bulk add questions; Attempts removed from the assign form
# ---------------------------------------------------------------------------

class TestQuestionAndAssignmentTests(_TestModuleBase):
    def test_multiple_questions_are_added_in_one_call(self):
        """Backs the picker's multi-select — one request carries the whole selection."""
        test = self._test_with([], status=Test.Status.DRAFT)
        questions = self._questions(5)
        self.client.force_authenticate(self.teacher)

        resp = self.client.post(
            f'/api/v1/tests/{test.pk}/add-questions/',
            {'question_ids': [q.pk for q in questions]},
            format='json',
        )

        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED), resp.data)
        self.assertEqual(test.test_questions.count(), 5)

    def test_assignment_creates_without_max_attempts(self):
        """The Assign form no longer sends it — the model default must carry it."""
        test = self._test_with(self._questions(2))
        self.client.force_authenticate(self.teacher)
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()

        resp = self.client.post(
            '/api/v1/tests/assignments/',
            {
                'test': test.pk,
                'assigned_to_type': TestAssignment.AssignedToType.CLASS,
                'school_class': self.klass.pk,
                'start_datetime': (now - timedelta(minutes=5)).isoformat(),
                'end_datetime': (now + timedelta(days=1)).isoformat(),
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(TestAssignment.objects.get(pk=resp.data['id']).max_attempts, 1)


# ---------------------------------------------------------------------------
# 6. An assigned test reaches the student immediately
# ---------------------------------------------------------------------------

class StudentAvailabilityTests(_TestModuleBase):
    def test_assigned_test_appears_for_the_student_at_once(self):
        test = self._test_with(self._questions(3))
        assignment = factories.make_assignment(test, self.klass, self.teacher)
        self.client.force_authenticate(self.student)

        resp = self.client.get('/api/v1/tests/assignments/?is_active=true')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(assignment.pk, [a['id'] for a in resp.data['results']])

    def test_and_can_be_started_straight_away(self):
        test = self._test_with(self._questions(3))
        assignment = factories.make_assignment(test, self.klass, self.teacher)
        self.client.force_authenticate(self.student)

        resp = self.client.post('/api/v1/exam/sessions/start/', {'assignment_id': assignment.pk})

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(
            ExamSession.objects.get(pk=resp.data['id']).status,
            ExamSession.Status.IN_PROGRESS,
        )

    def test_a_draft_test_is_not_offered(self):
        """Only published tests reach students — assignment alone is not enough."""
        test = self._test_with(self._questions(2), status=Test.Status.DRAFT)
        factories.make_assignment(test, self.klass, self.teacher)
        self.client.force_authenticate(self.student)

        resp = self.client.get('/api/v1/tests/assignments/?is_active=true')

        self.assertEqual(resp.data['count'], 0)

    def test_answers_are_prepopulated_for_every_question(self):
        """The exam screen relies on one ExamAnswer row per question existing up front."""
        test = self._test_with(self._questions(4), shuffle_questions=True)

        started = self._start(test)

        self.assertEqual(
            ExamAnswer.objects.filter(session_id=started['id']).count(), 4,
        )
        self.assertEqual(
            Decimal(str(started['test_duration_minutes'])), Decimal(test.duration_minutes),
        )
