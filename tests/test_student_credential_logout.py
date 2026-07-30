"""Changing a student's login credentials logs them out — item 6.

When a School Admin changes a student's Student ID or password, the student's outstanding
refresh tokens are blacklisted, so they can no longer refresh and must sign in again with the
new credentials. An edit that does NOT touch the credentials (or re-submits the same Student
ID) must leave their session alone — logging a student out over an unrelated edit would be a
regression.
"""
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .factories import make_class, make_school, make_section, make_student, make_user

REFRESH_URL = '/api/v1/auth/refresh/'


class StudentCredentialLogoutTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')
        self.admin = make_user('school_admin', self.school)
        self.student = make_student(
            self.school, self.klass, self.section, student_id='KAR_001-0009',
        )
        self.profile = self.student.student_profile

    def _issue_refresh(self) -> str:
        """Mint an outstanding refresh token for the student (as a login would)."""
        return str(RefreshToken.for_user(self.student))

    def _patch(self, **payload):
        self.client.force_authenticate(self.admin)
        return self.client.patch(
            f'/api/v1/students/{self.profile.pk}/', payload, format='json',
        )

    def _refresh_works(self, token: str) -> bool:
        # Unauthenticated refresh call — the token itself is the credential.
        self.client.force_authenticate(user=None)
        resp = self.client.post(REFRESH_URL, {'refresh': token}, format='json')
        return resp.status_code == status.HTTP_200_OK

    def test_changing_the_password_logs_the_student_out(self):
        token = self._issue_refresh()
        self.assertTrue(self._refresh_works(token))  # valid before the change

        resp = self._patch(password='BrandNew@2026X')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        self.assertFalse(self._refresh_works(token))  # blacklisted after the change

    def test_changing_the_student_id_logs_the_student_out(self):
        token = self._issue_refresh()
        resp = self._patch(student_id='KAR_001-0099')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertFalse(self._refresh_works(token))

    def test_an_unrelated_edit_keeps_the_student_logged_in(self):
        token = self._issue_refresh()
        resp = self._patch(parent_name='Updated Parent')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(self._refresh_works(token))

    def test_resubmitting_the_same_student_id_does_not_log_out(self):
        """Blank/unchanged Student ID means 'leave as-is' — not a credential change."""
        token = self._issue_refresh()
        resp = self._patch(student_id='KAR_001-0009')  # the current value
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(self._refresh_works(token))
