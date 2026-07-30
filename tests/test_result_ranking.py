"""Ranks must be right, and must be right by the time results are published.

Two defects are pinned here:

1. Ranking was sequential (1, 2, 3 ...), so two students with identical marks
   got different ranks decided by whichever row the database returned first.
   Equal performances are joint, and the tie must survive into the number the
   student is shown. Standard competition ranking: 1, 2, 2, 4.
2. Publishing never re-ranked. Ranks were maintained on submit and on evaluate
   only, so any cohort that reached publish with stale or missing ranks stayed
   that way — and publish is exactly when the teacher and the student read them.

Scope is deliberately the ASSIGNMENT, not the test: two sections sitting
separate windows are two cohorts, and each starts again at rank 1.
"""
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.exams.models import ExamSession
from apps.results.models import Result
from apps.results.services import rerank_assignment

from .factories import (
    make_assignment,
    make_chapter,
    make_class,
    make_question,
    make_result,
    make_school,
    make_section,
    make_session,
    make_student,
    make_subject,
    make_test,
    make_user,
)


class RankingTestCase(APITestCase):
    """One published test assigned to one section."""

    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')
        self.subject = make_subject(self.school, self.klass)
        self.chapter = make_chapter(self.subject)
        self.teacher = make_user('teacher', self.school)

        self.question = make_question(
            self.school, self.subject, self.chapter,
            correct_option='a', marks=Decimal('10'),
        )
        self.test = make_test(
            self.school, self.subject, self.klass, self.teacher,
            questions=[self.question],
        )
        self.assignment = make_assignment(self.test, self.klass, self.teacher)

    def _scored(self, marks, *, time_taken: int | None = None) -> Result:
        """A student who sat this assignment and scored `marks`."""
        student = make_student(self.school, self.klass, self.section)
        session = make_session(student, self.assignment, self.test)
        result = make_result(session, obtained=Decimal(str(marks)), total=Decimal('10'))
        if time_taken is not None:
            result.time_taken_seconds = time_taken
            result.save(update_fields=['time_taken_seconds'])
        return result

    def _ranks(self, assignment=None) -> list[tuple[Decimal, int | None]]:
        """(marks, rank) for one assignment, best first."""
        qs = Result.objects.filter(assignment=assignment or self.assignment)
        rows = qs.order_by('-obtained_marks', 'time_taken_seconds')
        return [(r.obtained_marks, r.rank) for r in rows]


class CompetitionRankingTests(RankingTestCase):
    """The tie-handling rule itself."""

    def test_distinct_scores_rank_sequentially(self):
        """The uncontroversial case must not regress."""
        self._scored(9)
        self._scored(7)
        self._scored(3)

        rerank_assignment(self.assignment.pk)

        self.assertEqual(
            [rank for _, rank in self._ranks()], [1, 2, 3],
        )

    def test_a_tie_shares_a_rank(self):
        """Equal marks and equal time is a genuine tie, not a coin flip."""
        self._scored(9)
        self._scored(7)
        self._scored(7)
        self._scored(3)

        rerank_assignment(self.assignment.pk)

        self.assertEqual(
            [rank for _, rank in self._ranks()], [1, 2, 2, 4],
        )

    def test_the_rank_after_a_tie_skips(self):
        """Standard competition ranking — three joint firsts push the next to 4."""
        self._scored(8)
        self._scored(8)
        self._scored(8)
        self._scored(1)

        rerank_assignment(self.assignment.pk)

        self.assertEqual(
            [rank for _, rank in self._ranks()], [1, 1, 1, 4],
        )

    def test_the_faster_student_wins_on_equal_marks(self):
        """Time is the tie-break, so equal marks alone is not a tie."""
        slow = self._scored(6, time_taken=900)
        fast = self._scored(6, time_taken=120)

        rerank_assignment(self.assignment.pk)

        fast.refresh_from_db()
        slow.refresh_from_db()
        self.assertEqual(fast.rank, 1)
        self.assertEqual(slow.rank, 2)

    def test_everyone_scoring_zero_is_joint_last(self):
        """A whole cohort tied at the bottom is still a tie."""
        self._scored(0)
        self._scored(0)

        rerank_assignment(self.assignment.pk)

        self.assertEqual([rank for _, rank in self._ranks()], [1, 1])

    def test_reranking_is_idempotent(self):
        """A second pass must report nothing to change — no needless writes."""
        self._scored(9)
        self._scored(4)

        self.assertEqual(rerank_assignment(self.assignment.pk), 2)
        self.assertEqual(rerank_assignment(self.assignment.pk), 0)

    def test_a_single_student_is_first(self):
        self._scored(5)
        rerank_assignment(self.assignment.pk)
        self.assertEqual([rank for _, rank in self._ranks()], [1])


class RankScopeTests(RankingTestCase):
    """Ranks belong to a cohort, and the cohort is the assignment."""

    def test_each_assignment_ranks_from_one(self):
        """A second section is a second ladder, not a continuation of the first."""
        other_section = make_section(self.school, self.klass, name='B')
        other_assignment = make_assignment(self.test, self.klass, self.teacher)

        self._scored(9)
        self._scored(5)

        for marks in (10, 4):
            student = make_student(self.school, self.klass, other_section)
            session = make_session(student, other_assignment, self.test)
            make_result(session, obtained=Decimal(str(marks)), total=Decimal('10'))

        rerank_assignment(self.assignment.pk)
        rerank_assignment(other_assignment.pk)

        self.assertEqual([rank for _, rank in self._ranks()], [1, 2])
        self.assertEqual([rank for _, rank in self._ranks(other_assignment)], [1, 2])

    def test_reranking_one_assignment_leaves_the_other_alone(self):
        other_assignment = make_assignment(self.test, self.klass, self.teacher)
        student = make_student(self.school, self.klass, self.section)
        session = make_session(student, other_assignment, self.test)
        untouched = make_result(session, obtained=Decimal('7'), total=Decimal('10'))
        self.assertIsNone(untouched.rank)

        self._scored(9)
        rerank_assignment(self.assignment.pk)

        untouched.refresh_from_db()
        self.assertIsNone(untouched.rank)


class PublishSettlesRanksTests(RankingTestCase):
    """Publishing must never expose a stale or empty Rank card."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.teacher)

    def _unranked_cohort(self) -> list[Result]:
        """Results deliberately left without ranks, as an un-reranked cohort would be."""
        results = [self._scored(9), self._scored(6), self._scored(6)]
        Result.objects.filter(assignment=self.assignment).update(rank=None)
        return results

    def test_publish_bulk_fills_in_missing_ranks(self):
        self._unranked_cohort()

        response = self.client.post(
            '/api/v1/results/publish-bulk/', {'assignment_id': self.assignment.pk},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.assertEqual([rank for _, rank in self._ranks()], [1, 2, 2])

    def test_publish_bulk_publishes_and_ranks_together(self):
        """The ranks the student sees and the visibility flag land in one step."""
        self._unranked_cohort()

        self.client.post(
            '/api/v1/results/publish-bulk/', {'assignment_id': self.assignment.pk},
        )

        for result in Result.objects.filter(assignment=self.assignment):
            self.assertTrue(result.is_published)
            self.assertIsNotNone(result.rank)

    def test_publishing_one_result_ranks_the_whole_cohort(self):
        """Rank is a position among peers — it cannot be computed one row at a time."""
        results = self._unranked_cohort()

        response = self.client.post(
            f'/api/v1/results/{results[0].pk}/publish/', {'is_published': True},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.assertEqual([rank for _, rank in self._ranks()], [1, 2, 2])

    def test_the_student_sees_the_rank_on_a_published_result(self):
        """End to end: the number reaching the student's Rank card is the real one."""
        results = self._unranked_cohort()
        self.client.post(
            '/api/v1/results/publish-bulk/', {'assignment_id': self.assignment.pk},
        )

        top = results[0]
        self.client.force_authenticate(top.student)
        response = self.client.get(f'/api/v1/results/{top.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['rank'], 1)


class SubmitStillRanksTests(RankingTestCase):
    """Publishing is a safety net, not a replacement for ranking on submit."""

    def test_ranks_exist_before_anything_is_published(self):
        self._scored(9)
        self._scored(4)
        rerank_assignment(self.assignment.pk)

        for result in Result.objects.filter(assignment=self.assignment):
            self.assertFalse(result.is_published)
            self.assertIsNotNone(result.rank)

    def test_a_late_submission_reorders_the_cohort(self):
        """A new top scorer pushes everyone down — ranks are not write-once."""
        first = self._scored(5)
        rerank_assignment(self.assignment.pk)
        first.refresh_from_db()
        self.assertEqual(first.rank, 1)

        self._scored(9)
        rerank_assignment(self.assignment.pk)

        first.refresh_from_db()
        self.assertEqual(first.rank, 2)


class CompletedAttemptIsFinalTests(RankingTestCase):
    """The retake lock is an integrity rule, not an attempts limit.

    `max_attempts` no longer gates anything — availability is the window alone —
    but a student who already produced a Result for this cohort must not produce
    a second one, or they would occupy two places in the ranking above.
    """

    def test_a_student_who_finished_cannot_start_again(self):
        student = make_student(self.school, self.klass, self.section)
        session = make_session(student, self.assignment, self.test)
        make_result(session, obtained=Decimal('5'), total=Decimal('10'))

        self.client.force_authenticate(student)
        response = self.client.post(
            '/api/v1/exam/sessions/start/', {'assignment_id': self.assignment.pk},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already completed', str(response.data).lower())

    def test_the_message_no_longer_mentions_an_attempts_limit(self):
        """The wording is user-facing: an attempts cap is no longer the reason."""
        student = make_student(self.school, self.klass, self.section)
        make_session(student, self.assignment, self.test)

        self.client.force_authenticate(student)
        response = self.client.post(
            '/api/v1/exam/sessions/start/', {'assignment_id': self.assignment.pk},
        )

        self.assertNotIn('maximum attempts', str(response.data).lower())

    def test_raising_max_attempts_grants_nothing(self):
        """The field is inert: a cohort's second attempt stays blocked either way."""
        self.assignment.max_attempts = 5
        self.assignment.save(update_fields=['max_attempts'])

        student = make_student(self.school, self.klass, self.section)
        make_session(
            student, self.assignment, self.test, status=ExamSession.Status.SUBMITTED,
        )

        self.client.force_authenticate(student)
        response = self.client.post(
            '/api/v1/exam/sessions/start/', {'assignment_id': self.assignment.pk},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_fresh_student_can_still_start(self):
        """The control — the lock must not block a first attempt."""
        student = make_student(self.school, self.klass, self.section)

        self.client.force_authenticate(student)
        response = self.client.post(
            '/api/v1/exam/sessions/start/', {'assignment_id': self.assignment.pk},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
