"""Every "School ID" in the app must be the school's CODE, never its primary key.

`School.code` (e.g. KAR_001) is the identifier people use — it prefixes every
student login ID the school issues. `School.pk` is an internal join key that means
nothing to a teacher reading their own profile, yet it was what the profile screen
printed under the label "School ID".

These tests exist because the fix is a field name away from regressing: dropping
`school_code` from a serializer leaves the UI silently falling back to the number.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from .factories import (
    make_class,
    make_school,
    make_section,
    make_student,
    make_user,
)


class SchoolCodeIsExposedTests(APITestCase):
    """The API must hand out the code wherever a school is identified."""

    def setUp(self):
        self.school = make_school()
        self.school.name = 'Karapettai Nadar Hr Sec School'
        self.school.code = 'KAR_001'
        self.school.save(update_fields=['name', 'code'])

        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')
        self.admin = make_user('school_admin', self.school)
        self.teacher = make_user('teacher', self.school)
        self.student = make_student(self.school, self.klass, self.section)

    def test_auth_me_carries_the_school_code(self):
        """The profile screen's source — this is where the PK used to be shown."""
        self.client.force_authenticate(self.teacher)
        response = self.client.get('/api/v1/auth/me/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['school_code'], 'KAR_001')

    def test_auth_me_still_carries_the_name_and_pk(self):
        """The code is an ADDITION — the PK is still needed for joins and filters."""
        self.client.force_authenticate(self.teacher)
        response = self.client.get('/api/v1/auth/me/')

        self.assertEqual(response.data['school'], self.school.pk)
        self.assertEqual(response.data['school_name'], self.school.name)

    def test_the_code_is_not_the_primary_key(self):
        """Guards the confusion itself, not just the presence of a field."""
        self.client.force_authenticate(self.teacher)
        response = self.client.get('/api/v1/auth/me/')

        self.assertNotEqual(str(response.data['school_code']), str(self.school.pk))

    def test_a_user_with_no_school_gets_null(self):
        """CSC Admin belongs to no school — that must not break the payload."""
        csc_admin = make_user('csc_admin', None)
        self.client.force_authenticate(csc_admin)
        response = self.client.get('/api/v1/auth/me/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data['school_code'])

    def test_student_list_carries_the_school_code(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/v1/students/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['results'][0]['school_code'], 'KAR_001')

    def test_student_detail_carries_the_school_code(self):
        self.client.force_authenticate(self.admin)
        profile_id = self.student.student_profile.pk
        response = self.client.get(f'/api/v1/students/{profile_id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['school_code'], 'KAR_001')

    def test_teacher_list_carries_the_school_code(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/v1/teachers/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rows = response.data['results']
        if rows:
            self.assertEqual(rows[0]['school_code'], 'KAR_001')

    def test_the_school_endpoint_still_reports_its_own_code(self):
        """The original source of truth — unchanged, and asserted so it stays that way."""
        self.client.force_authenticate(self.admin)
        response = self.client.get(f'/api/v1/schools/{self.school.pk}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['code'], 'KAR_001')
