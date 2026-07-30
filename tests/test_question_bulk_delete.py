"""Bulk delete in the Question Bank.

Two things can go silently wrong here and neither is caught by the type checker:
1. Ids come from the client, so an unscoped filter would let one school delete
   another school's questions by guessing primary keys.
2. Questions used by a test/exam are PROTECTed. Deleting them must archive rather
   than destroy exam history — and one in-use question must not abort the batch.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.questions.models import Question
from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import (
    make_assignment,
    make_chapter,
    make_class,
    make_question,
    make_school,
    make_section,
    make_subject,
    make_test,
    make_user,
)

User = get_user_model()


class QuestionBulkDeleteTests(APITestCase):
    def setUp(self):
        self.url = reverse('question-bulk-delete')

        self.school = make_school()
        self.school_class = make_class(self.school)
        self.subject = make_subject(self.school, self.school_class)
        self.chapter = make_chapter(self.subject)
        self.teacher = make_user(User.Role.TEACHER, self.school)
        # A teacher's Question Bank is scoped to their assigned classes, so this teacher must
        # be assigned to the class whose questions the tests act on — otherwise the bank is
        # (correctly) empty for them and every delete finds nothing.
        profile = TeacherProfile.objects.create(
            user=self.teacher, school=self.school, teacher_id='BULK_TR_001',
        )
        TeacherAssignment.objects.create(teacher=profile, school_class=self.school_class)
        self.client.force_authenticate(self.teacher)

    def _question(self):
        return make_question(self.school, self.subject, self.chapter, created_by=self.teacher)

    def test_deletes_several_unused_questions_at_once(self):
        ids = [self._question().pk for _ in range(3)]

        resp = self.client.post(self.url, {'ids': ids}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'deleted': 3, 'archived': 0, 'skipped': 0})
        self.assertFalse(Question.objects.filter(pk__in=ids).exists())

    def test_in_use_question_is_archived_and_does_not_abort_the_batch(self):
        """A ProtectedError must not poison the transaction for the rest of the batch."""
        in_use = self._question()
        unused_a = self._question()
        unused_b = self._question()
        # Referencing the question from a test makes it PROTECTed.
        make_test(
            self.school, self.subject, self.school_class, self.teacher, questions=[in_use],
        )

        resp = self.client.post(
            self.url,
            {'ids': [in_use.pk, unused_a.pk, unused_b.pk]},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'deleted': 2, 'archived': 1, 'skipped': 0})

        in_use.refresh_from_db()
        self.assertFalse(in_use.is_active)  # archived, exam history intact
        self.assertFalse(Question.objects.filter(pk__in=[unused_a.pk, unused_b.pk]).exists())

    def test_cannot_delete_another_schools_questions(self):
        other_school = make_school()
        other_class = make_class(other_school)
        other_subject = make_subject(other_school, other_class)
        other_chapter = make_chapter(other_subject)
        foreign = make_question(other_school, other_subject, other_chapter)
        mine = self._question()

        resp = self.client.post(self.url, {'ids': [mine.pk, foreign.pk]}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'deleted': 1, 'archived': 0, 'skipped': 1})
        # The other school's question survives untouched.
        self.assertTrue(Question.objects.filter(pk=foreign.pk).exists())
        self.assertFalse(Question.objects.filter(pk=mine.pk).exists())

    def test_students_cannot_bulk_delete(self):
        question = self._question()
        student = make_user(User.Role.STUDENT, self.school)
        self.client.force_authenticate(student)

        resp = self.client.post(self.url, {'ids': [question.pk]}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Question.objects.filter(pk=question.pk).exists())

    def test_empty_id_list_is_rejected(self):
        resp = self.client.post(self.url, {'ids': []}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_ids_are_counted_once(self):
        question = self._question()

        resp = self.client.post(
            self.url, {'ids': [question.pk, question.pk]}, format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {'deleted': 1, 'archived': 0, 'skipped': 0})

    def test_single_delete_still_works(self):
        """The existing one-by-one delete must be unchanged."""
        question = self._question()

        resp = self.client.delete(reverse('question-detail', args=[question.pk]))

        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Question.objects.filter(pk=question.pk).exists())

    def test_assignment_backed_question_is_archived_not_destroyed(self):
        """Guard exam history: a question behind a live assignment survives as archived."""
        question = self._question()
        test = make_test(
            self.school, self.subject, self.school_class, self.teacher, questions=[question],
        )
        section = make_section(self.school, self.school_class)
        make_assignment(test, self.school_class, self.teacher, section=section)

        resp = self.client.post(self.url, {'ids': [question.pk]}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['archived'], 1)
        question.refresh_from_db()
        self.assertFalse(question.is_active)
