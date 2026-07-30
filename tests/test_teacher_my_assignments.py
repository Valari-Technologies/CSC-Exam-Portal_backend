"""/teachers/my-assignments/ returns only the teacher's assigned classes and sections.

This backs the Class/Section filters on Teacher -> Students, which must not offer classes or
sections the School Admin never assigned this teacher (item 2).
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import make_class, make_school, make_section, make_user

URL = '/api/v1/teachers/my-assignments/'


class TeacherMyAssignmentsTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.class10 = make_class(self.school, numeric_value=10)
        self.c10a = make_section(self.school, self.class10, name='A')
        self.c10b = make_section(self.school, self.class10, name='B')
        self.class9 = make_class(self.school, numeric_value=9)
        self.c9a = make_section(self.school, self.class9, name='A')
        self.c9c = make_section(self.school, self.class9, name='C')

        self.admin = make_user('school_admin', self.school)
        teacher_user = make_user('teacher', self.school)
        self.profile = TeacherProfile.objects.create(
            user=teacher_user, school=self.school, teacher_id='T1',
        )
        self.teacher = teacher_user

    def _assign(self, school_class, section):
        TeacherAssignment.objects.create(
            teacher=self.profile, school_class=school_class, section=section,
            assigned_by=self.admin,
        )

    def test_returns_only_assigned_classes_and_sections(self):
        self._assign(self.class10, self.c10b)  # 10-B only
        self.client.force_authenticate(self.teacher)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        class_ids = [c['id'] for c in response.data['classes']]
        self.assertEqual(class_ids, [self.class10.pk])
        self.assertEqual(response.data['sections'], ['B'])  # NOT A — unassigned

    def test_multiple_assignments_are_deduped_and_sorted(self):
        self._assign(self.class10, self.c10a)
        self._assign(self.class9, self.c9c)
        self.client.force_authenticate(self.teacher)
        response = self.client.get(URL)
        self.assertEqual(sorted(c['id'] for c in response.data['classes']),
                         sorted([self.class10.pk, self.class9.pk]))
        self.assertEqual(response.data['sections'], ['A', 'C'])

    def test_whole_class_assignment_expands_to_all_its_sections(self):
        """A section-less (whole-class) assignment means every section of that class."""
        self._assign(self.class10, None)
        self.client.force_authenticate(self.teacher)
        response = self.client.get(URL)
        self.assertEqual([c['id'] for c in response.data['classes']], [self.class10.pk])
        self.assertEqual(response.data['sections'], ['A', 'B'])  # both sections of class 10

    def test_no_assignments_returns_empty(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.get(URL)
        self.assertEqual(response.data['classes'], [])
        self.assertEqual(response.data['sections'], [])

    def test_non_teacher_is_forbidden(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
