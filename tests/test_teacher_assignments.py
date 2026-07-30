"""Managing an existing teacher's class assignments via /teachers/assignments/.

This is the endpoint the School Admin's Edit Teacher → Assign Class section drives. The
create-teacher form already allows a class-only assignment (no subject), so the same must
work here — the regression this pins is a subject-less add that used to 500.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import make_class, make_school, make_section, make_subject, make_user


class TeacherAssignmentEndpointTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.admin = make_user('school_admin', self.school)
        self.teacher_user = make_user('teacher', self.school)
        self.teacher = TeacherProfile.objects.create(
            user=self.teacher_user, school=self.school, teacher_id='X_TR_001',
        )
        self.klass = make_class(self.school)
        self.section = make_section(self.school, self.klass)
        self.subject = make_subject(self.school, self.klass)
        self.client.force_authenticate(self.admin)

    def _post(self, **overrides):
        payload = {
            'teacher': self.teacher.id,
            'school_class': self.klass.id,
            'academic_year': '2025-26',
        }
        payload.update(overrides)
        return self.client.post('/api/v1/teachers/assignments/', payload)

    def test_class_only_assignment_without_subject_is_created(self):
        """The regression: no subject must be a clean 201, not a 500."""
        resp = self._post()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        assignment = TeacherAssignment.objects.get(teacher=self.teacher)
        self.assertIsNone(assignment.subject)

    def test_subject_bound_assignment_is_created(self):
        resp = self._post(subject=self.subject.id, section=self.section.id)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_duplicate_class_only_assignment_is_rejected_with_400(self):
        self.assertEqual(self._post().status_code, status.HTTP_201_CREATED)
        resp = self._post()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TeacherAssignment.objects.filter(teacher=self.teacher).count(), 1)

    def test_class_only_duplicate_across_academic_years_is_rejected_with_400(self):
        """The partial unique constraints ignore academic_year for subject-less rows, so a
        same-class/section add with a different year must 400, never reach the DB and 500."""
        self.assertEqual(self._post(academic_year='2025-26').status_code, status.HTTP_201_CREATED)
        resp = self._post(academic_year='2026-27')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TeacherAssignment.objects.filter(teacher=self.teacher).count(), 1)

    def test_assignment_can_be_removed(self):
        self._post()
        assignment = TeacherAssignment.objects.get(teacher=self.teacher)
        resp = self.client.delete(f'/api/v1/teachers/assignments/{assignment.id}/')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TeacherAssignment.objects.filter(pk=assignment.id).exists())

    def test_cannot_assign_a_class_from_another_school(self):
        other_class = make_class(make_school())
        resp = self._post(school_class=other_class.id)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_section_must_belong_to_the_selected_class(self):
        other_class = make_class(self.school)
        stray_section = make_section(self.school, other_class)
        resp = self._post(section=stray_section.id)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_teacher_cannot_manage_assignments(self):
        self.client.force_authenticate(self.teacher_user)
        resp = self._post()
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
