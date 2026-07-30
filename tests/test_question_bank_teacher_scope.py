"""A teacher's Question Bank is limited to the subjects assigned to them.

Scoping lives in QuestionViewSet.get_queryset, so it applies to the list, single reads, and
the count endpoint alike — the Question Bank UI, test creation, and counts all see the same
restricted set. Two assignment shapes are honoured (see TeacherAssignment): a subject-bound
row grants exactly that subject; a whole-class row (no subject named) grants every subject of
that class. Admins are unaffected.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import (
    make_chapter,
    make_class,
    make_question,
    make_school,
    make_section,
    make_subject,
    make_user,
)


class TeacherQuestionScopeTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.class_a = make_class(self.school)
        self.class_b = make_class(self.school)
        # Class A has two subjects; the teacher will be subject-bound to only one of them.
        self.subject_a1 = make_subject(self.school, self.class_a)
        self.subject_a2 = make_subject(self.school, self.class_a)
        self.subject_b = make_subject(self.school, self.class_b)
        self.q_a1 = make_question(self.school, self.subject_a1, make_chapter(self.subject_a1))
        self.q_a2 = make_question(self.school, self.subject_a2, make_chapter(self.subject_a2))
        self.q_b = make_question(self.school, self.subject_b, make_chapter(self.subject_b))

        self.teacher_user = make_user('teacher', self.school)
        self.teacher = TeacherProfile.objects.create(
            user=self.teacher_user, school=self.school, teacher_id='X_TR_001',
        )

    def _assign_subject(self, subject, school_class):
        TeacherAssignment.objects.create(
            teacher=self.teacher, subject=subject, school_class=school_class,
        )

    def _assign_whole_class(self, school_class):
        TeacherAssignment.objects.create(teacher=self.teacher, school_class=school_class)

    def test_subject_bound_assignment_narrows_to_that_subject(self):
        """Assigned Class A / subject_a1 only — subject_a2 (same class) stays hidden."""
        self._assign_subject(self.subject_a1, self.class_a)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get('/api/v1/questions/')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {q['id'] for q in resp.data['results']}
        self.assertEqual(ids, {self.q_a1.id})

    def test_whole_class_assignment_grants_every_subject_of_that_class(self):
        """A class-only assignment (no subject) covers all subjects of the class."""
        self._assign_whole_class(self.class_a)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get('/api/v1/questions/')

        ids = {q['id'] for q in resp.data['results']}
        self.assertEqual(ids, {self.q_a1.id, self.q_a2.id})
        self.assertNotIn(self.q_b.id, ids)

    def test_count_is_scoped_to_assigned_subjects(self):
        self._assign_subject(self.subject_a1, self.class_a)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get('/api/v1/questions/count/')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)

    def test_cannot_retrieve_an_unassigned_subject_question(self):
        """Subject-bound to subject_a1 — a sibling subject in the same class is 404."""
        self._assign_subject(self.subject_a1, self.class_a)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get(f'/api/v1/questions/{self.q_a2.id}/')

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_mixed_assignments_union_across_subjects_and_classes(self):
        """Subject-bound to Class A / subject_a1, plus whole Class B → a1 + all of B."""
        self._assign_subject(self.subject_a1, self.class_a)
        self._assign_whole_class(self.class_b)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get('/api/v1/questions/')

        ids = {q['id'] for q in resp.data['results']}
        self.assertEqual(ids, {self.q_a1.id, self.q_b.id})
        self.assertNotIn(self.q_a2.id, ids)

    def test_section_on_assignment_does_not_change_subject_scope(self):
        """A section-scoped, subject-bound assignment still grants its subject."""
        section = make_section(self.school, self.class_a)
        TeacherAssignment.objects.create(
            teacher=self.teacher, subject=self.subject_a1,
            school_class=self.class_a, section=section,
        )
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get('/api/v1/questions/')

        ids = {q['id'] for q in resp.data['results']}
        self.assertEqual(ids, {self.q_a1.id})

    def test_teacher_with_no_assignments_sees_nothing(self):
        lonely = make_user('teacher', self.school)
        TeacherProfile.objects.create(
            user=lonely, school=self.school, teacher_id='X_TR_002',
        )
        self.client.force_authenticate(lonely)

        resp = self.client.get('/api/v1/questions/')

        self.assertEqual(resp.data['count'], 0)

    def test_school_admin_still_sees_all_school_questions(self):
        admin = make_user('school_admin', self.school)
        self.client.force_authenticate(admin)

        resp = self.client.get('/api/v1/questions/')

        ids = {q['id'] for q in resp.data['results']}
        self.assertIn(self.q_a1.id, ids)
        self.assertIn(self.q_a2.id, ids)
        self.assertIn(self.q_b.id, ids)
