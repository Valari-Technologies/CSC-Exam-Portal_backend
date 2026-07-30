"""Sign in with Google (/auth/google/) — OAuth 2.0 id_token verification and account linking.

Google's tokeninfo endpoint is mocked so the REAL verification logic runs without a network
call. The security-critical assertions are the audience/issuer checks: an id_token is only
acceptable if Google minted it for THIS application.

Accounts are never auto-created — a Google identity only signs in if it already maps to a
provisioned user, which is what keeps the school's roster the single source of truth.
"""
from unittest import mock

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.schools.models import School

from .factories import make_class, make_school, make_section, make_student, make_user

URL = '/api/v1/auth/google/'
CLIENT_ID = 'test-client-id.apps.googleusercontent.com'
CONFIGURED = {
    'GOOGLE_CLIENT_ID': CLIENT_ID,
    'SOCIALACCOUNT_PROVIDERS': {'google': {'APP': {'client_id': CLIENT_ID, 'secret': ''}}},
}


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


def tokeninfo(**overrides) -> dict:
    """A well-formed Google tokeninfo payload; override a key to break one rule at a time."""
    data = {
        'aud': CLIENT_ID,
        'iss': 'https://accounts.google.com',
        'sub': '110000000000000000001',
        'email': 'linked@example.com',
        'email_verified': 'true',
        'name': 'Linked User',
        'picture': 'https://example.com/avatar.png',
    }
    data.update(overrides)
    return data


def patch_tokeninfo(payload: dict, status_code: int = 200):
    return mock.patch(
        'apps.authentication.services.requests.get',
        return_value=_FakeResponse(payload, status_code),
    )


@override_settings(**CONFIGURED)
class GoogleTokenVerificationTests(APITestCase):
    """The token itself must prove it was issued by Google, for this app."""

    def _post(self):
        return self.client.post(URL, {'id_token': 'any'}, format='json')

    def test_a_token_for_another_app_is_rejected(self):
        """The core check: an id_token minted for a DIFFERENT client id must not sign anyone in."""
        make_user('teacher', make_school(), email='linked@example.com')
        with patch_tokeninfo(tokeninfo(aud='someone-elses-client-id')):
            response = self._post()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_untrusted_issuer_is_rejected(self):
        with patch_tokeninfo(tokeninfo(iss='https://evil.example.com')):
            response = self._post()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unverified_email_is_rejected(self):
        with patch_tokeninfo(tokeninfo(email_verified=False)):
            response = self._post()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_token_google_rejects_is_rejected(self):
        with patch_tokeninfo({'error': 'invalid_token'}, status_code=400):
            response = self._post()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_id_token_is_required(self):
        response = self.client.post(URL, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('id_token', response.data)


class GoogleUnconfiguredTests(APITestCase):
    """With no client id configured there is no audience to verify against — fail CLOSED."""

    @override_settings(
        GOOGLE_CLIENT_ID='',
        SOCIALACCOUNT_PROVIDERS={'google': {'APP': {'client_id': '', 'secret': ''}}},
    )
    def test_sign_in_is_refused_when_no_client_id_is_configured(self):
        make_user('teacher', make_school(), email='linked@example.com')
        # A perfectly valid token for some OTHER app must still not get in.
        with patch_tokeninfo(tokeninfo(aud='someone-elses-client-id')):
            response = self.client.post(URL, {'id_token': 'any'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(**CONFIGURED)
class GoogleAccountLinkingTests(APITestCase):
    def setUp(self):
        self.school = make_school()

    def test_an_existing_user_is_matched_by_email_and_linked(self):
        user = make_user('teacher', self.school, email='linked@example.com')
        with patch_tokeninfo(tokeninfo()):
            response = self.client.post(URL, {'id_token': 'any'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn('access', response.data)
        self.assertEqual(response.data['user']['email'], 'linked@example.com')

        user.refresh_from_db()
        self.assertEqual(user.oauth_provider, 'google')
        self.assertEqual(user.oauth_id, '110000000000000000001')

    def test_a_returning_user_is_matched_by_google_id_even_if_the_email_changed(self):
        user = make_user('teacher', self.school, email='old.address@example.com')
        user.oauth_provider = 'google'
        user.oauth_id = '110000000000000000001'
        user.save(update_fields=['oauth_provider', 'oauth_id'])

        with patch_tokeninfo(tokeninfo()):
            response = self.client.post(URL, {'id_token': 'any'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['user']['id'], user.pk)

    def test_an_unknown_google_account_is_told_what_to_do(self):
        """No auto-create: the response must guide the person, not just fail."""
        with patch_tokeninfo(tokeninfo(email='stranger@example.com')):
            response = self.client.post(URL, {'id_token': 'any'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('detail', response.data)
        self.assertIn('school admin', response.data['detail'].lower())

    def test_a_deactivated_user_cannot_sign_in(self):
        user = make_user('teacher', self.school, email='linked@example.com')
        user.is_active = False
        user.save(update_fields=['is_active'])

        with patch_tokeninfo(tokeninfo()):
            response = self.client.post(URL, {'id_token': 'any'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_suspended_school_blocks_google_the_same_as_password_login(self):
        """Google is a third login door — it must honour school status like the other two."""
        suspended = make_school(status=School.Status.SUSPENDED)
        make_user('teacher', suspended, email='linked@example.com')

        with patch_tokeninfo(tokeninfo()):
            response = self.client.post(URL, {'id_token': 'any'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


@override_settings(**CONFIGURED)
class GoogleRolesTests(APITestCase):
    """Every role signs in through the same door; the response carries the role the SPA
    redirects on."""

    def setUp(self):
        self.school = make_school()

    def _sign_in_as(self, user):
        with patch_tokeninfo(tokeninfo(email=user.email)):
            return self.client.post(URL, {'id_token': 'any'}, format='json')

    def test_super_admin(self):
        response = self._sign_in_as(make_user('csc_admin', None, email='linked@example.com'))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['user']['role'], 'csc_admin')

    def test_school_admin(self):
        response = self._sign_in_as(
            make_user('school_admin', self.school, email='linked@example.com'))
        self.assertEqual(response.data['user']['role'], 'school_admin')

    def test_teacher(self):
        response = self._sign_in_as(
            make_user('teacher', self.school, email='linked@example.com'))
        self.assertEqual(response.data['user']['role'], 'teacher')

    def test_student_with_an_email_on_file(self):
        """Students may have no email at all — Google only works for those who do."""
        klass = make_class(self.school, numeric_value=10)
        section = make_section(self.school, klass, name='A')
        student = make_student(self.school, klass, section, email='linked@example.com')
        response = self._sign_in_as(student)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['user']['role'], 'student')


@override_settings(**CONFIGURED)
class PasswordLoginIsUnaffectedTests(APITestCase):
    """Adding Google must not disturb the email+password door."""

    def test_password_login_still_works(self):
        from .factories import DEFAULT_PASSWORD

        school = make_school()
        user = make_user('school_admin', school, email='pw@example.com')
        response = self.client.post('/api/v1/auth/login/', {
            'email': user.email, 'password': DEFAULT_PASSWORD,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
