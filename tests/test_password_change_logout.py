"""Changing your own password ends the session — for every role.

`/auth/password/change/` blacklists the user's outstanding refresh tokens, so the session they
changed the password from can no longer be refreshed and they must sign in again with the new
password. The frontend also clears its local tokens and redirects to the login page; this file
pins the SERVER half, which is what actually makes the old session unusable.

Note the deliberate limit: JWT access tokens are stateless, so an already-issued access token
stays valid until it expires (30 min). Refresh is what's revoked here.
"""
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .factories import (
    DEFAULT_PASSWORD,
    make_class,
    make_school,
    make_section,
    make_student,
    make_user,
)

CHANGE_URL = '/api/v1/auth/password/change/'
REFRESH_URL = '/api/v1/auth/refresh/'
LOGIN_URL = '/api/v1/auth/login/'
STUDENT_LOGIN_URL = '/api/v1/auth/student/login/'
NEW_PASSWORD = 'BrandNew@2026X'


class PasswordChangeLogoutTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')

    # -- helpers --------------------------------------------------------------

    def _refresh_works(self, token: str) -> bool:
        """The refresh token IS the credential here, so call it unauthenticated."""
        self.client.force_authenticate(user=None)
        response = self.client.post(REFRESH_URL, {'refresh': token}, format='json')
        return response.status_code == status.HTTP_200_OK

    def _change_password(self, user):
        self.client.force_authenticate(user)
        return self.client.post(CHANGE_URL, {
            'old_password': DEFAULT_PASSWORD,
            'new_password': NEW_PASSWORD,
        }, format='json')

    def _assert_session_ends(self, user):
        token = str(RefreshToken.for_user(user))
        self.assertTrue(self._refresh_works(token), 'token should be valid before the change')

        response = self._change_password(user)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.assertFalse(self._refresh_works(token), 'token must be revoked after the change')

    # -- every role -----------------------------------------------------------

    def test_super_admin_is_logged_out(self):
        self._assert_session_ends(make_user('csc_admin', None))

    def test_school_admin_is_logged_out(self):
        self._assert_session_ends(make_user('school_admin', self.school))

    def test_teacher_is_logged_out(self):
        self._assert_session_ends(make_user('teacher', self.school))

    def test_student_is_logged_out(self):
        self._assert_session_ends(make_student(self.school, self.klass, self.section))

    # -- the credentials actually changed -------------------------------------

    def test_old_password_is_rejected_and_the_new_one_works(self):
        user = make_user('school_admin', self.school)
        self._change_password(user)
        self.client.force_authenticate(user=None)

        old = self.client.post(
            LOGIN_URL, {'email': user.email, 'password': DEFAULT_PASSWORD}, format='json',
        )
        self.assertEqual(old.status_code, status.HTTP_400_BAD_REQUEST)

        new = self.client.post(
            LOGIN_URL, {'email': user.email, 'password': NEW_PASSWORD}, format='json',
        )
        self.assertEqual(new.status_code, status.HTTP_200_OK, new.data)

    def test_student_signs_in_again_with_the_new_password(self):
        """Students re-authenticate at the Student ID door, not the email one."""
        student = make_student(
            self.school, self.klass, self.section, student_id='KAR_001-0100',
        )
        self._change_password(student)
        self.client.force_authenticate(user=None)

        response = self.client.post(STUDENT_LOGIN_URL, {
            'student_id': 'KAR_001-0100',
            'password': NEW_PASSWORD,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_all_of_a_users_sessions_are_revoked_not_just_the_current_one(self):
        """A password change should kill every device, not only the one that changed it."""
        user = make_user('teacher', self.school)
        first = str(RefreshToken.for_user(user))
        second = str(RefreshToken.for_user(user))

        self._change_password(user)

        self.assertFalse(self._refresh_works(first))
        self.assertFalse(self._refresh_works(second))
