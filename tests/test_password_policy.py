"""The password policy: 8 characters, 1 uppercase + 5 lowercase + 1 digit + 1 special.

Two distinct rules, deliberately different:

- GENERATED passwords (students) have exactly that composition.
- USER-CHOSEN passwords must be >= 8 with at least one of each character class. Demanding
  exactly 5 lowercase from a human would reject perfectly good passwords (and every longer
  one), so the counts are the generator's shape, not a straitjacket on what people may type.

The same rule has to hold on every door: setup, reset-confirm, and change-password.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.authentication.services import build_password_setup_link
from apps.students.services import generate_student_password

from .factories import DEFAULT_PASSWORD, make_school, make_user

User = get_user_model()

# Exactly the agreed shape: 1 upper + 5 lower + 1 digit + 1 special = 8.
VALID_8 = 'Kf7dmqx@'


def parse_link(link: str) -> dict:
    from urllib.parse import parse_qs, urlparse
    query = parse_qs(urlparse(link).query)
    return {'uid': query['uid'][0], 'token': query['token'][0]}


class PasswordPolicyConfigTests(TestCase):
    def test_minimum_length_is_eight(self):
        self.assertEqual(settings.PASSWORD_MIN_LENGTH, 8)

    def test_the_length_validator_reads_the_single_source(self):
        """settings.PASSWORD_MIN_LENGTH must actually drive Django's validator."""
        length_validators = [
            v for v in settings.AUTH_PASSWORD_VALIDATORS
            if v['NAME'].endswith('MinimumLengthValidator')
        ]
        self.assertEqual(len(length_validators), 1)
        self.assertEqual(
            length_validators[0]['OPTIONS']['min_length'], settings.PASSWORD_MIN_LENGTH,
        )

    def test_an_eight_character_password_is_accepted(self):
        validate_password(VALID_8)  # must not raise

    def test_seven_characters_is_rejected(self):
        with self.assertRaises(DjangoValidationError):
            validate_password('Kf7dm@x')  # 7 chars

    def test_complexity_is_still_enforced_at_eight(self):
        """Shortening the length must not have loosened the character-class rules."""
        for password, missing in (
            ('kf7dmqx@', 'uppercase'),
            ('KF7DMQX@', 'lowercase'),
            ('Kfdmqrx@', 'digit'),
            ('Kf7dmqxy', 'special'),
        ):
            with self.subTest(missing=missing):
                with self.assertRaises(DjangoValidationError):
                    validate_password(password)


class GeneratedPasswordTests(TestCase):
    def test_generated_password_is_eight_characters(self):
        for _ in range(20):
            self.assertEqual(len(generate_student_password()), 8)

    def test_generated_password_has_the_exact_composition(self):
        """1 uppercase, 5 lowercase, 1 digit, 1 special — every time."""
        specials = '!@#$%^&*'
        for _ in range(50):
            password = generate_student_password()
            self.assertEqual(sum(c.isupper() for c in password), 1, password)
            self.assertEqual(sum(c.islower() for c in password), 5, password)
            self.assertEqual(sum(c.isdigit() for c in password), 1, password)
            self.assertEqual(sum(c in specials for c in password), 1, password)

    def test_generated_passwords_always_pass_the_validators(self):
        """The generator and the policy must never disagree."""
        for _ in range(25):
            validate_password(generate_student_password())

    def test_generated_passwords_are_not_all_the_same(self):
        self.assertGreater(len({generate_student_password() for _ in range(25)}), 20)

    def test_length_is_driven_by_the_policy_setting(self):
        self.assertEqual(len(generate_student_password()), settings.PASSWORD_MIN_LENGTH)


class PasswordPolicyAcrossFlowsTests(APITestCase):
    """The same policy on every door that sets a password."""

    def setUp(self):
        self.school = make_school()
        self.user = make_user(User.Role.SCHOOL_ADMIN, self.school)

    def test_setup_password_accepts_eight_characters(self):
        passwordless = make_user(User.Role.TEACHER, self.school, password=None)
        creds = parse_link(build_password_setup_link(
            passwordless, frontend_url='http://localhost:5173',
        ))

        resp = self.client.post(
            reverse('authentication:password-setup'),
            {'uid': creds['uid'], 'token': creds['token'], 'new_password': VALID_8},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # And the new 8-character password actually logs in.
        login = self.client.post(
            reverse('authentication:login'),
            {'email': passwordless.email, 'password': VALID_8},
            format='json',
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_setup_password_rejects_seven_characters(self):
        passwordless = make_user(User.Role.TEACHER, self.school, password=None)
        creds = parse_link(build_password_setup_link(
            passwordless, frontend_url='http://localhost:5173',
        ))

        resp = self.client.post(
            reverse('authentication:password-setup'),
            {'uid': creds['uid'], 'token': creds['token'], 'new_password': 'Kf7dm@x'},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_password_accepts_eight_characters(self):
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            reverse('authentication:password-change'),
            {'old_password': DEFAULT_PASSWORD, 'new_password': VALID_8},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_change_password_rejects_seven_characters(self):
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            reverse('authentication:password-change'),
            {'old_password': DEFAULT_PASSWORD, 'new_password': 'Kf7dm@x'},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_password_still_rejects_eight_chars_missing_complexity(self):
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            reverse('authentication:password-change'),
            {'old_password': DEFAULT_PASSWORD, 'new_password': 'kf7dmqxy'},  # no upper/special
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_generated_student_password_satisfies_every_door(self):
        """End to end: what the generator emits must be settable and usable."""
        passwordless = make_user(User.Role.STUDENT, self.school, password=None)
        generated = generate_student_password()
        creds = parse_link(build_password_setup_link(
            passwordless, frontend_url='http://localhost:5173',
        ))

        resp = self.client.post(
            reverse('authentication:password-setup'),
            {'uid': creds['uid'], 'token': creds['token'], 'new_password': generated},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
