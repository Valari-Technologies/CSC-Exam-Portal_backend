"""Teacher provisioning — the same password-setup-link flow as School Admin creation.

A teacher is created WITHOUT a password. A one-time setup link is generated, emailed to the
teacher's login email, and surfaced on the create response so the School Admin can hand it
over. The teacher clicks it, sets a password, and only then can log in.
"""
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.db.utils import IntegrityError
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherProfile

from .factories import make_school, make_user

User = get_user_model()

# The password the teacher chooses via their setup link — never supplied at create time.
CHOSEN_PASSWORD = 'Teacher@2026Pass'


def parse_setup_link(link: str) -> dict:
    """Pull uid + token out of a /setup-password?uid=..&token=.. link."""
    query = parse_qs(urlparse(link).query)
    return {'uid': query['uid'][0], 'token': query['token'][0]}


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TeacherProvisioningTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.school_admin = make_user(User.Role.SCHOOL_ADMIN, self.school)
        self.client.force_authenticate(self.school_admin)
        self.url = reverse('teacher-list')

    def payload(self, **overrides) -> dict:
        data = {
            'email': 'new.teacher@school.edu',
            'full_name': 'New Teacher',
            # Item 2: Add Teacher requires every field.
            'employee_id': 'EMP-1',
            'gender': 'male',
            'qualification': 'M.Sc',
            'joining_date': '2020-06-01',
        }
        data.update(overrides)
        return data

    def test_create_provisions_passwordless_teacher(self):
        resp = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        teacher = User.objects.get(email='new.teacher@school.edu')
        self.assertEqual(teacher.role, User.Role.TEACHER)
        self.assertEqual(teacher.school_id, self.school.id)
        # No password is chosen at create time — the teacher sets their own via the link.
        self.assertFalse(teacher.has_usable_password())
        self.assertFalse(teacher.is_password_set)
        self.assertFalse(teacher.must_change_password)
        self.assertTrue(TeacherProfile.objects.filter(user=teacher).exists())

    def test_create_returns_setup_link_and_emails_the_teacher(self):
        mail.outbox = []
        resp = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # The link is surfaced to the School Admin on the create response...
        self.assertIn('setup_link', resp.data)
        self.assertIn('/setup-password', resp.data['setup_link'])
        # ...and emailed to the teacher's login email.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['new.teacher@school.edu'])

    def test_teacher_sets_password_via_link_then_logs_in(self):
        resp = self.client.post(self.url, self.payload(), format='json')
        creds = parse_setup_link(resp.data['setup_link'])
        self.client.force_authenticate(None)

        # No password yet — login must fail before setup.
        pre = self.client.post(
            reverse('authentication:login'),
            {'email': 'new.teacher@school.edu', 'password': CHOSEN_PASSWORD},
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

        # Now login with the login email works, and no forced change is pending.
        login = self.client.post(
            reverse('authentication:login'),
            {'email': 'new.teacher@school.edu', 'password': CHOSEN_PASSWORD},
            format='json',
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertEqual(login.data['user']['role'], 'teacher')
        self.assertFalse(login.data['user']['must_change_password'])

    def test_setup_link_is_null_on_reads(self):
        created = self.client.post(self.url, self.payload(), format='json')
        teacher_id = created.data['id']

        detail = self.client.get(reverse('teacher-detail', args=[teacher_id]))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        # The one-time link is only ever on the create response, never on a later read.
        self.assertIsNone(detail.data['setup_link'])

    def test_duplicate_email_is_rejected(self):
        make_user(User.Role.TEACHER, self.school, email='taken@school.edu')
        resp = self.client.post(
            self.url, self.payload(email='taken@school.edu'), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_password_is_accepted_or_echoed_back(self):
        resp = self.client.post(self.url, self.payload(), format='json')
        self.assertNotIn('password', resp.data)


class TeacherIdGenerationTests(APITestCase):
    """Teacher IDs are derived from the School ID: KAR_001 -> KAR_TR_001, KAR_TR_002, ...

    The sequence runs independently per school. Employee ID is a separate, admin-owned
    field and must be entirely unaffected by any of this.
    """

    def setUp(self):
        self.school = make_school(code='KAR_001', name='Karapettai Nadar Hr.Sec.School')
        self.school_admin = make_user(User.Role.SCHOOL_ADMIN, self.school)
        self.client.force_authenticate(self.school_admin)
        self.url = reverse('teacher-list')

    def create_teacher(self, email: str, **extra) -> dict:
        data = {
            'email': email,
            'full_name': 'A Teacher',
            # Item 2: Add Teacher requires every field; overridable per test.
            'employee_id': 'EMP-1',
            'gender': 'male',
            'qualification': 'M.Sc',
            'joining_date': '2020-06-01',
        }
        data.update(extra)
        return self.client.post(self.url, data, format='json')

    def test_teacher_id_is_generated_from_the_school_id(self):
        resp = self.create_teacher('t1@school.edu')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['teacher_id'], 'KAR_TR_001')

    def test_sequence_increments_within_a_school(self):
        for index in range(3):
            self.create_teacher(f'seq{index}@school.edu')
        ids = list(
            TeacherProfile.objects.filter(school=self.school)
            .order_by('teacher_id').values_list('teacher_id', flat=True)
        )
        self.assertEqual(ids, ['KAR_TR_001', 'KAR_TR_002', 'KAR_TR_003'])

    def test_sequences_are_independent_per_school(self):
        self.create_teacher('kar@school.edu')
        self.create_teacher('kar2@school.edu')

        # A second school starts its own sequence at 001.
        other = make_school(code='GRE_001', name='Green Valley School')
        self.client.force_authenticate(make_user(User.Role.SCHOOL_ADMIN, other))
        resp = self.create_teacher('gre@green.edu')

        self.assertEqual(resp.data['teacher_id'], 'GRE_TR_001')
        self.assertEqual(
            list(TeacherProfile.objects.filter(school=self.school)
                 .order_by('teacher_id').values_list('teacher_id', flat=True)),
            ['KAR_TR_001', 'KAR_TR_002'],
        )

    def test_teacher_id_is_not_accepted_from_the_client(self):
        resp = self.create_teacher('hack@school.edu', teacher_id='HACKED_999')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['teacher_id'], 'KAR_TR_001')
        self.assertFalse(TeacherProfile.objects.filter(teacher_id='HACKED_999').exists())

    def test_teacher_id_is_not_editable(self):
        created = self.create_teacher('edit@school.edu')
        teacher_id = created.data['id']

        resp = self.client.patch(
            reverse('teacher-detail', args=[teacher_id]),
            {'teacher_id': 'CHANGED_001'},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        profile = TeacherProfile.objects.get(pk=teacher_id)
        self.assertEqual(profile.teacher_id, 'KAR_TR_001')

    def test_legacy_school_code_still_yields_a_three_letter_prefix(self):
        # A School ID from before the current scheme mixes in digits — the prefix must
        # still be three LETTERS (VGS003 -> VGS), not 'VGS003'.
        legacy = make_school(code='VGS003', name='VGS girls hr sec school')
        self.client.force_authenticate(make_user(User.Role.SCHOOL_ADMIN, legacy))

        resp = self.create_teacher('legacy@vgs.edu')

        self.assertEqual(resp.data['teacher_id'], 'VGS_TR_001')

    def test_employee_id_is_unchanged_and_independent(self):
        """The Employee ID must keep working exactly as before — it is not the Teacher ID."""
        resp = self.create_teacher('emp@school.edu', employee_id='EMP-42')

        self.assertEqual(resp.data['employee_id'], 'EMP-42')
        self.assertEqual(resp.data['teacher_id'], 'KAR_TR_001')

        # Still freely editable, and editing it leaves the Teacher ID alone.
        patched = self.client.patch(
            reverse('teacher-detail', args=[resp.data['id']]),
            {'employee_id': 'EMP-99'},
            format='json',
        )
        self.assertEqual(patched.status_code, status.HTTP_200_OK)
        profile = TeacherProfile.objects.get(pk=resp.data['id'])
        self.assertEqual(profile.employee_id, 'EMP-99')
        self.assertEqual(profile.teacher_id, 'KAR_TR_001')

    def test_employee_id_is_required_on_create(self):
        """Item 2: Add Teacher requires every field, including Employee ID."""
        resp = self.client.post(self.url, {
            'email': 'noemp@school.edu', 'full_name': 'A Teacher',
            'gender': 'male', 'qualification': 'M.Sc', 'joining_date': '2020-06-01',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('employee_id', resp.data)

    def test_teacher_id_appears_on_list_and_detail_reads(self):
        created = self.create_teacher('read@school.edu')
        teacher_id = created.data['id']

        detail = self.client.get(reverse('teacher-detail', args=[teacher_id]))
        self.assertEqual(detail.data['teacher_id'], 'KAR_TR_001')

        listing = self.client.get(self.url)
        self.assertEqual(listing.data['results'][0]['teacher_id'], 'KAR_TR_001')

    def test_duplicate_teacher_id_in_one_school_is_rejected_by_the_db(self):
        """The generator walks forward, but the constraint is the backstop behind it."""
        self.create_teacher('dup1@school.edu')
        user = make_user(User.Role.TEACHER, self.school, email='dup2@school.edu')

        with self.assertRaises(IntegrityError):
            TeacherProfile.objects.create(
                user=user, school=self.school, teacher_id='KAR_TR_001',
            )

    def test_same_teacher_id_may_exist_in_a_different_school(self):
        # Uniqueness is scoped to the school, so two schools sharing a three-letter prefix
        # each run their own sequence. Not a credential, so this is harmless.
        self.create_teacher('a@school.edu')
        other = make_school(code='KAR_002', name='Karapettai Second School')
        user = make_user(User.Role.TEACHER, other, email='b@other.edu')

        TeacherProfile.objects.create(user=user, school=other, teacher_id='KAR_TR_001')

        self.assertEqual(TeacherProfile.objects.filter(teacher_id='KAR_TR_001').count(), 2)
