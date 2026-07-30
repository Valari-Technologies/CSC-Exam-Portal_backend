"""School provisioning + the login/contact email separation.

The architectural rule these tests defend: a School's official_email is a CONTACT address
and can never authenticate anyone; a School Admin's login email lives on the User and is
the only thing that can. Creating a school provisions both, atomically.
"""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.schools.models import School

from .factories import make_class, make_school, make_section, make_student, make_user

User = get_user_model()

# The password the new admin chooses via their setup link — never supplied at create time.
CHOSEN_PASSWORD = 'SchoolAdm1n@2026'


def school_payload(**overrides) -> dict:
    # No 'code': the School ID is generated server-side from the name.
    payload = {
        'name': 'Provision Test School',
        'address': '1 Provision Way',
        'city': 'Chennai',
        'state': 'TN',
        'pincode': '600001',
        'official_email': 'office@prov.edu',      # contact address
        'contact_phone': '9876543210',
        'admin_full_name': 'Prov Admin',
        'admin_email': 'prov.admin@prov.edu',     # LOGIN address — deliberately different
        'status': 'active',
    }
    payload.update(overrides)
    return payload


def parse_setup_link(link: str) -> dict:
    """Pull uid + token out of a /setup-password?uid=..&token=.. link."""
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(link).query)
    return {'uid': query['uid'][0], 'token': query['token'][0]}


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SchoolProvisioningTests(APITestCase):
    def setUp(self):
        self.csc_admin = make_user(User.Role.CSC_ADMIN, None)
        self.client.force_authenticate(self.csc_admin)
        self.url = reverse('school-list')

    def test_create_school_provisions_passwordless_school_admin(self):
        mail.outbox = []
        resp = self.client.post(self.url, school_payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        school = School.objects.get(name='Provision Test School')
        admin = User.objects.get(email='prov.admin@prov.edu')

        # The two emails are stored in different places and are not the same value.
        self.assertEqual(school.official_email, 'office@prov.edu')
        self.assertEqual(admin.email, 'prov.admin@prov.edu')
        self.assertNotEqual(school.official_email, admin.email)

        self.assertEqual(admin.role, User.Role.SCHOOL_ADMIN)
        self.assertEqual(admin.school_id, school.id)
        # No password is chosen at create time — the admin sets their own via the link.
        self.assertFalse(admin.has_usable_password())
        self.assertFalse(admin.is_password_set)
        self.assertFalse(admin.must_change_password)

    def test_create_returns_setup_link_and_emails_the_admin(self):
        mail.outbox = []
        resp = self.client.post(self.url, school_payload(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # The link is surfaced to the CSC Admin on the create response...
        self.assertIn('admin_setup_link', resp.data)
        self.assertIn('/setup-password', resp.data['admin_setup_link'])
        # ...and emailed to the admin's LOGIN email, never the school's contact email.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['prov.admin@prov.edu'])
        self.assertNotIn('office@prov.edu', mail.outbox[0].to)

    def test_no_password_is_accepted_or_echoed_back(self):
        resp = self.client.post(self.url, school_payload(), format='json')
        # The write-only password field is gone; it is never part of the response.
        self.assertNotIn('admin_password', resp.data)

    def test_duplicate_admin_email_rolls_back_the_whole_school(self):
        make_user(User.Role.TEACHER, make_school(), email='taken@x.com')
        resp = self.client.post(
            self.url,
            school_payload(name='Rollback One School', admin_email='taken@x.com'),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        # Nothing may survive a failed create.
        self.assertFalse(School.objects.filter(name='Rollback One School').exists())

    def test_missing_admin_email_rolls_back_the_whole_school(self):
        payload = school_payload(name='Rollback Two School')
        payload.pop('admin_email')
        resp = self.client.post(self.url, payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(School.objects.filter(name='Rollback Two School').exists())

    def test_admin_sets_password_via_link_then_logs_in_with_login_email(self):
        resp = self.client.post(self.url, school_payload(), format='json')
        creds = parse_setup_link(resp.data['admin_setup_link'])
        self.client.force_authenticate(None)

        # The admin has no password yet — login must fail before setup.
        pre = self.client.post(
            reverse('authentication:login'),
            {'email': 'prov.admin@prov.edu', 'password': CHOSEN_PASSWORD},
            format='json',
        )
        self.assertEqual(pre.status_code, status.HTTP_400_BAD_REQUEST)

        # Use the setup link to choose a password.
        setup = self.client.post(
            reverse('authentication:password-setup'),
            {'uid': creds['uid'], 'token': creds['token'], 'new_password': CHOSEN_PASSWORD},
            format='json',
        )
        self.assertEqual(setup.status_code, status.HTTP_200_OK)

        # Now login with the LOGIN email works, and no forced change is pending.
        resp = self.client.post(
            reverse('authentication:login'),
            {'email': 'prov.admin@prov.edu', 'password': CHOSEN_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['user']['role'], 'school_admin')
        self.assertFalse(resp.data['user']['must_change_password'])

    def test_official_email_can_never_be_used_to_log_in(self):
        resp = self.client.post(self.url, school_payload(), format='json')
        creds = parse_setup_link(resp.data['admin_setup_link'])
        self.client.force_authenticate(None)

        # Even after the admin sets a real password, the school's contact email is not a
        # credential and can never authenticate.
        self.client.post(
            reverse('authentication:password-setup'),
            {'uid': creds['uid'], 'token': creds['token'], 'new_password': CHOSEN_PASSWORD},
            format='json',
        )
        resp = self.client.post(
            reverse('authentication:login'),
            {'email': 'office@prov.edu', 'password': CHOSEN_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_editing_school_cannot_change_admin_login_email(self):
        self.client.post(self.url, school_payload(), format='json')
        school = School.objects.get(name='Provision Test School')

        resp = self.client.patch(
            reverse('school-detail', args=[school.id]),
            {'admin_email': 'hijack@evil.com'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.filter(email='prov.admin@prov.edu').exists())
        self.assertFalse(User.objects.filter(email='hijack@evil.com').exists())

    def test_editing_official_email_touches_only_the_school(self):
        self.client.post(self.url, school_payload(), format='json')
        school = School.objects.get(name='Provision Test School')

        resp = self.client.patch(
            reverse('school-detail', args=[school.id]),
            {'official_email': 'newoffice@prov.edu'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        school.refresh_from_db()
        admin = User.objects.get(email='prov.admin@prov.edu')
        self.assertEqual(school.official_email, 'newoffice@prov.edu')
        self.assertEqual(admin.email, 'prov.admin@prov.edu')  # login email untouched


class PasswordResetRoutingTests(APITestCase):
    """Reset links always go to the user's own login email — never the school's."""

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_school_admin_reset_goes_to_login_email_not_official_email(self):
        school = make_school(official_email='office@sch.edu')
        admin = make_user(User.Role.SCHOOL_ADMIN, school, email='admin.login@sch.edu')

        mail.outbox = []
        resp = self.client.post(
            reverse('authentication:password-reset'),
            {'email': admin.email},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(mail.outbox[0].to, [admin.email])
        self.assertNotIn(school.official_email, mail.outbox[0].to)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_student_resets_by_student_id_and_mail_goes_to_their_own_email(self):
        school = make_school(official_email='office@sch2.edu')
        school_class = make_class(school)
        section = make_section(school, school_class)
        student = make_student(
            school, school_class, section, email='pupil@home.com', student_id='SCH2-0001',
        )

        mail.outbox = []
        resp = self.client.post(
            reverse('authentication:password-reset-student'),
            {'student_id': 'SCH2-0001'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(mail.outbox[0].to, [student.email])

    def test_unknown_student_id_still_returns_200(self):
        """No enumeration: an unknown Student ID looks identical to a known one."""
        resp = self.client.post(
            reverse('authentication:password-reset-student'),
            {'student_id': 'NOPE-9999'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class MustChangePasswordTests(APITestCase):
    def test_changing_password_clears_the_forced_change_flag(self):
        user = make_user(
            User.Role.TEACHER, make_school(), password='TempPass@2026', email='temp@x.com',
        )
        user.must_change_password = True
        user.save(update_fields=['must_change_password'])

        self.client.force_authenticate(user)
        resp = self.client.post(
            reverse('authentication:password-change'),
            {'old_password': 'TempPass@2026', 'new_password': 'MyOwnPass@2026'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password('MyOwnPass@2026'))

    def test_existing_users_are_not_forced_to_change(self):
        """Backward compatibility: the flag defaults to False for everyone who already existed."""
        user = make_user(User.Role.TEACHER, make_school(), email='existing@x.com')
        self.assertFalse(user.must_change_password)
