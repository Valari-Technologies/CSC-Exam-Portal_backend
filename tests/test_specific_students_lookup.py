"""Assign Test -> Specific Students must resolve ANY student in the school by exact ID.

Item 5: a teacher assigning a test to specific students could not select students outside
their assigned class/sections (which is where bulk-imported students often land). The
teacher's list is still scoped to their sections, but an EXACT Student-ID lookup now
resolves any student in the same school — matching the feature (assign to named recipients)
and the assignment API (which already accepts any same-school recipient).
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import make_class, make_school, make_section, make_student, make_user


class SpecificStudentLookupTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.sec_a = make_section(self.school, self.klass, name='A')
        self.sec_b = make_section(self.school, self.klass, name='B')

        self.in_scope = make_student(self.school, self.klass, self.sec_b, student_id='SCH-B1')
        self.out_scope = make_student(self.school, self.klass, self.sec_a, student_id='SCH-A1')

        self.admin = make_user('school_admin', self.school)
        teacher_user = make_user('teacher', self.school)
        profile = TeacherProfile.objects.create(
            user=teacher_user, school=self.school, teacher_id='T1',
        )
        # Assigned to section B only — section A is out of this teacher's scope.
        TeacherAssignment.objects.create(
            teacher=profile, school_class=self.klass, section=self.sec_b, assigned_by=self.admin,
        )
        self.teacher = teacher_user

    def _lookup(self, sid):
        return self.client.get('/api/v1/students/', {'user__student_id': sid})

    def test_teacher_resolves_out_of_scope_student_by_exact_id(self):
        """The bug: this student (section A) used to be invisible to the teacher."""
        self.client.force_authenticate(self.teacher)
        response = self._lookup('SCH-A1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['student_id'], 'SCH-A1')

    def test_teacher_resolves_in_scope_student_by_exact_id(self):
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self._lookup('SCH-B1').data['count'], 1)

    def test_plain_list_stays_teacher_scoped(self):
        """Broadening is ONLY for the exact-ID lookup — browsing is still restricted."""
        self.client.force_authenticate(self.teacher)
        response = self.client.get('/api/v1/students/')
        ids = [r['student_id'] for r in response.data['results']]
        self.assertIn('SCH-B1', ids)
        self.assertNotIn('SCH-A1', ids)

    def test_lookup_is_still_school_scoped(self):
        """A student in another school is never resolvable, even by exact ID."""
        other_school = make_school()
        other_class = make_class(other_school)
        other_section = make_section(other_school, other_class, name='A')
        make_student(other_school, other_class, other_section, student_id='OTHER-1')
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self._lookup('OTHER-1').data['count'], 0)

    def test_admin_resolves_any_student(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self._lookup('SCH-A1').data['count'], 1)
