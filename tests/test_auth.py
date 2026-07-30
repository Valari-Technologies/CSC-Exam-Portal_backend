"""Auth endpoint tests — login success/failure and protected-route gating."""

from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from tests import factories


class LoginAPITests(APITestCase):
    def setUp(self):
        # DRF throttling uses the default cache; clear it so the 5/min login
        # throttle from a previous test never leaks into this one.
        cache.clear()
        self.school = factories.make_school()
        self.user = factories.make_user("school_admin", self.school, email="login@example.com")

    def test_login_success_returns_tokens_and_user(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {
                "email": "login@example.com",
                "password": factories.DEFAULT_PASSWORD,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        self.assertEqual(resp.data["user"]["email"], "login@example.com")

    def test_login_wrong_password_rejected(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {
                "email": "login@example.com",
                "password": "WrongPassword123!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", resp.data)

    def test_me_requires_authentication(self):
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
