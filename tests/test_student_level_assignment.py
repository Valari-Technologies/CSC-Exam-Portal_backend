"""Assigning a test to named students, and the ID lookup that picks them.

Two things are pinned here.

**The lookup is exact.** The Assign Test page resolves a typed Student ID to one
student. `search` matches by substring, which is right for a search box and wrong
here: "CSC001-001" would also return "CSC001-0010", and choosing the wrong row
sends the paper to the wrong child. `?user__student_id=` is the exact lookup the
page uses instead.

**The assignment reaches exactly the named students.** A student-level assignment
carries no class or section, so the only thing standing between a classmate and
someone else's paper is the recipient rows — which makes "a classmate cannot see
it" a case worth asserting rather than assuming.
"""
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.tests.models import TestAssignment, TestAssignmentStudent

from .factories import (
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


class StudentIdLookupTests(APITestCase):
    """`?user__student_id=` must name one student and no near misses."""

    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')
        self.admin = make_user('school_admin', self.school)

        # Deliberately confusable: one ID is a prefix of the other.
        self.short = make_student(
            self.school, self.klass, self.section, student_id='CSC001-001',
        )
        self.long = make_student(
            self.school, self.klass, self.section, student_id='CSC001-0010',
        )
        self.client.force_authenticate(self.admin)

    def _ids(self, **params) -> list[str]:
        response = self.client.get('/api/v1/students/', params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [row['student_id'] for row in response.data['results']]

    def test_exact_lookup_ignores_the_longer_id(self):
        """The regression this filter exists for."""
        self.assertEqual(self._ids(user__student_id='CSC001-001'), ['CSC001-001'])

    def test_exact_lookup_finds_the_longer_id_too(self):
        self.assertEqual(self._ids(user__student_id='CSC001-0010'), ['CSC001-0010'])

    def test_search_still_matches_both(self):
        """The contrast that justifies the separate filter — search is a substring."""
        self.assertCountEqual(
            self._ids(search='CSC001-001'), ['CSC001-001', 'CSC001-0010'],
        )

    def test_an_unknown_id_returns_nothing(self):
        """What the page turns into "No student found with ID ...".."""
        self.assertEqual(self._ids(user__student_id='NOPE-9999'), [])

    def test_the_lookup_is_school_scoped(self):
        """A teacher cannot resolve an ID belonging to another school."""
        other_school = make_school()
        other_class = make_class(other_school, numeric_value=10)
        other_section = make_section(other_school, other_class, name='A')
        make_student(
            other_school, other_class, other_section, student_id='OTHER-0001',
        )

        self.assertEqual(self._ids(user__student_id='OTHER-0001'), [])


class StudentLevelTestCase(APITestCase):
    """Shared graph: one published test and two classmates, one of whom gets picked."""

    URL = '/api/v1/tests/assignments/'

    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')
        self.subject = make_subject(self.school, self.klass)
        self.chapter = make_chapter(self.subject)
        self.teacher = make_user('teacher', self.school)

        self.question = make_question(
            self.school, self.subject, self.chapter,
            correct_option='a', marks=Decimal('2'),
        )
        self.test = make_test(
            self.school, self.subject, self.klass, self.teacher,
            questions=[self.question],
        )

        self.picked = make_student(
            self.school, self.klass, self.section, student_id='CSC001-0002',
        )
        # Same class and section as `picked` — so only the recipient rows separate them.
        self.classmate = make_student(
            self.school, self.klass, self.section, student_id='CSC001-0003',
        )

        self.client.force_authenticate(self.teacher)

    def _payload(self, students, **overrides) -> dict:
        payload = {
            'test': self.test.pk,
            'assigned_to_type': 'students',
            'start_datetime': '2026-01-01T09:00:00Z',
            'end_datetime': '2030-01-01T10:00:00Z',
            'students': students,
        }
        payload.update(overrides)
        return payload

    def _create(self, students, **overrides):
        return self.client.post(self.URL, self._payload(students, **overrides), format='json')


class StudentLevelAssignmentTests(StudentLevelTestCase):
    """Creating the assignment."""

    def test_a_test_can_be_assigned_to_one_student(self):
        response = self._create([self.picked.pk])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        assignment = TestAssignment.objects.get(pk=response.data['id'])
        self.assertEqual(assignment.assigned_to_type, TestAssignment.AssignedToType.STUDENTS)
        self.assertEqual(
            list(assignment.student_assignments.values_list('student_id', flat=True)),
            [self.picked.pk],
        )

    def test_a_test_can_be_assigned_to_several_students(self):
        response = self._create([self.picked.pk, self.classmate.pk])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        self.assertEqual(TestAssignmentStudent.objects.count(), 2)

    def test_no_class_or_section_is_recorded(self):
        """The recipients ARE the scope — a leftover class would be a phantom one."""
        response = self._create([self.picked.pk])

        assignment = TestAssignment.objects.get(pk=response.data['id'])
        self.assertIsNone(assignment.school_class_id)
        self.assertIsNone(assignment.section_id)

    def test_an_empty_recipient_list_is_refused(self):
        response = self._create([])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('students', response.data)

    def test_a_student_from_another_school_is_refused(self):
        other_school = make_school()
        other_class = make_class(other_school, numeric_value=10)
        other_section = make_section(other_school, other_class, name='A')
        outsider = make_student(other_school, other_class, other_section)

        response = self._create([self.picked.pk, outsider.pk])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TestAssignment.objects.count(), 0)


class StudentLevelVisibilityTests(StudentLevelTestCase):
    """Only the named students see it, and only they can start it."""

    def setUp(self):
        super().setUp()
        response = self._create([self.picked.pk])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assignment_id = response.data['id']

    def _visible_to(self, student) -> list[int]:
        self.client.force_authenticate(student)
        response = self.client.get(self.URL, {'is_active': 'true'})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [row['id'] for row in response.data['results']]

    def test_the_named_student_sees_it(self):
        self.assertIn(self.assignment_id, self._visible_to(self.picked))

    def test_a_classmate_does_not_see_it(self):
        """Same class, same section, not named — the recipient rows are the only gate."""
        self.assertNotIn(self.assignment_id, self._visible_to(self.classmate))

    def test_the_named_student_can_start(self):
        self.client.force_authenticate(self.picked)
        response = self.client.post(
            '/api/v1/exam/sessions/start/', {'assignment_id': self.assignment_id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_a_classmate_cannot_start_even_knowing_the_id(self):
        """Hiding it from the list is not enough — the start endpoint must refuse too."""
        self.client.force_authenticate(self.classmate)
        response = self.client.post(
            '/api/v1/exam/sessions/start/', {'assignment_id': self.assignment_id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_window_still_governs_a_named_student(self):
        """Being named grants entry to the exam, not exemption from its schedule."""
        assignment = TestAssignment.objects.get(pk=self.assignment_id)
        assignment.end_datetime = assignment.start_datetime
        assignment.save(update_fields=['end_datetime'])

        self.client.force_authenticate(self.picked)
        response = self.client.post(
            '/api/v1/exam/sessions/start/', {'assignment_id': self.assignment_id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('closed', str(response.data).lower())
