"""Student ID and Teacher ID appear on the owner's own profile (/auth/me/) — items 3 & 4.

student_id was already exposed; teacher_id is new and computed defensively so /auth/me never
500s for the roles that have no teacher profile.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherProfile

from .factories import make_class, make_school, make_section, make_student, make_user

ME_URL = '/api/v1/auth/me/'


class ProfileIdsTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')

    def test_student_sees_their_student_id(self):
        student = make_student(self.school, self.klass, self.section, student_id='KAR_001-0007')
        self.client.force_authenticate(student)
        response = self.client.get(ME_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['student_id'], 'KAR_001-0007')
        self.assertIsNone(response.data['teacher_id'])

    def test_teacher_sees_their_teacher_id(self):
        teacher_user = make_user('teacher', self.school)
        TeacherProfile.objects.create(
            user=teacher_user, school=self.school, teacher_id='KAR_TR_005',
        )
        self.client.force_authenticate(teacher_user)
        response = self.client.get(ME_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['teacher_id'], 'KAR_TR_005')

    def test_admin_has_no_teacher_id_and_me_does_not_error(self):
        """The SerializerMethodField runs for every user — a non-teacher must not 500 it."""
        admin = make_user('school_admin', self.school)
        self.client.force_authenticate(admin)
        response = self.client.get(ME_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data['teacher_id'])
        self.assertIsNone(response.data['student_id'])

    def test_teacher_without_a_profile_row_does_not_error(self):
        """A teacher User with no TeacherProfile still loads — teacher_id is just null."""
        orphan = make_user('teacher', self.school)  # no TeacherProfile created
        self.client.force_authenticate(orphan)
        response = self.client.get(ME_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data['teacher_id'])
