"""Additional Details / support-request workflow — items 6 & 7.

A School Admin raises a request; the Super Admin is notified, reviews, and replies; the
reply reaches the School Admin as an in-app notification. The whole exchange is IN-APP:
these tests assert that NO email is ever sent, which is the standing rule for anything that
could otherwise touch School.official_email (a contact address, never a credential).
"""
from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notifications.models import Notification
from apps.support.models import SupportRequest

from .factories import make_class, make_school, make_section, make_student, make_user


class SupportRequestTestCase(APITestCase):
    URL = '/api/v1/support-requests/'

    def setUp(self):
        self.school_a = make_school()
        self.school_b = make_school()
        self.admin_a = make_user('school_admin', self.school_a)
        self.admin_b = make_user('school_admin', self.school_b)
        self.csc_admin = make_user('csc_admin', None)
        self.teacher = make_user('teacher', self.school_a)

        klass = make_class(self.school_a)
        section = make_section(self.school_a, klass, name='A')
        self.student = make_student(self.school_a, klass, section)

    def _create(self, **overrides) -> SupportRequest:
        """Create a request directly (bypassing the API) for read/reply tests."""
        data = dict(
            school=self.school_a,
            raised_by=self.admin_a,
            issue_type=SupportRequest.IssueType.INCORRECT_SCHOOL_NAME,
            description='Our school name is misspelt.',
        )
        data.update(overrides)
        return SupportRequest.objects.create(**data)


class CreateSupportRequestTests(SupportRequestTestCase):
    def test_school_admin_creates_a_request(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(self.URL, {
            'issue_type': 'incorrect_school_name',
            'description': 'The school name is spelt wrong.',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        request = SupportRequest.objects.get()
        self.assertEqual(request.school_id, self.school_a.pk)
        self.assertEqual(request.raised_by_id, self.admin_a.pk)
        self.assertEqual(request.status, SupportRequest.Status.OPEN)

    def test_school_is_derived_server_side_not_from_the_client(self):
        """A School Admin cannot raise a request against another school by sending its id."""
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(self.URL, {
            'school': self.school_b.pk,  # should be ignored — school is read-only
            'issue_type': 'other',
            'description': 'Trying to target another school.',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(SupportRequest.objects.get().school_id, self.school_a.pk)

    def test_super_admin_is_notified_on_create(self):
        self.client.force_authenticate(self.admin_a)
        self.client.post(self.URL, {
            'issue_type': 'password_issue',
            'description': 'Password problem.',
        }, format='json')
        note = Notification.objects.filter(
            user=self.csc_admin, type=Notification.Type.SUPPORT_REQUEST,
        ).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.data.get('school_code'), self.school_a.code)

    def test_description_is_required(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(self.URL, {
            'issue_type': 'other', 'description': '   ',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('description', response.data)

    def test_teacher_cannot_create(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.post(self.URL, {
            'issue_type': 'other', 'description': 'x',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_create(self):
        self.client.force_authenticate(self.student)
        response = self.client.post(self.URL, {
            'issue_type': 'other', 'description': 'x',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_creating_sends_no_email(self):
        self.client.force_authenticate(self.admin_a)
        self.client.post(self.URL, {
            'issue_type': 'incorrect_login_email',
            'description': 'Login email is wrong.',
        }, format='json')
        self.assertEqual(len(mail.outbox), 0)


class ListSupportRequestTests(SupportRequestTestCase):
    def test_super_admin_sees_all_requests(self):
        self._create()
        self._create(school=self.school_b, raised_by=self.admin_b)
        self.client.force_authenticate(self.csc_admin)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)

    def test_school_admin_sees_only_their_own_schools_requests(self):
        mine = self._create()
        self._create(school=self.school_b, raised_by=self.admin_b)
        self.client.force_authenticate(self.admin_a)
        response = self.client.get(self.URL)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], mine.pk)

    def test_super_admin_can_filter_by_status(self):
        self._create()  # open
        self._create(status=SupportRequest.Status.RESOLVED)
        self.client.force_authenticate(self.csc_admin)
        response = self.client.get(self.URL, {'status': 'open'})
        self.assertEqual(response.data['count'], 1)

    def test_student_cannot_list(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ReplySupportRequestTests(SupportRequestTestCase):
    def _reply_url(self, pk: int) -> str:
        return f'{self.URL}{pk}/reply/'

    def test_super_admin_reply_resolves_and_notifies_school_admin(self):
        request = self._create()
        self.client.force_authenticate(self.csc_admin)
        response = self.client.post(self._reply_url(request.pk), {
            'reply': 'Fixed the spelling — please verify.',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        request.refresh_from_db()
        self.assertEqual(request.status, SupportRequest.Status.RESOLVED)
        self.assertEqual(request.admin_reply, 'Fixed the spelling — please verify.')
        self.assertEqual(request.resolved_by_id, self.csc_admin.pk)
        self.assertIsNotNone(request.resolved_at)

        note = Notification.objects.filter(
            user=self.admin_a, type=Notification.Type.SUPPORT_REQUEST,
        ).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.message, 'Fixed the spelling — please verify.')

    def test_reply_without_resolve_keeps_it_open(self):
        request = self._create()
        self.client.force_authenticate(self.csc_admin)
        self.client.post(self._reply_url(request.pk), {
            'reply': 'Looking into it.', 'resolve': False,
        }, format='json')
        request.refresh_from_db()
        self.assertEqual(request.status, SupportRequest.Status.OPEN)
        self.assertEqual(request.admin_reply, 'Looking into it.')

    def test_reply_message_is_required(self):
        request = self._create()
        self.client.force_authenticate(self.csc_admin)
        response = self.client.post(self._reply_url(request.pk), {
            'reply': '   ',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_school_admin_cannot_reply(self):
        request = self._create()
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(self._reply_url(request.pk), {
            'reply': 'I will fix it myself.',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_replying_sends_no_email(self):
        request = self._create()
        self.client.force_authenticate(self.csc_admin)
        self.client.post(self._reply_url(request.pk), {
            'reply': 'Resolved.',
        }, format='json')
        self.assertEqual(len(mail.outbox), 0)


class DeleteSupportRequestTests(SupportRequestTestCase):
    def test_school_admin_delete_is_soft_delete_and_hides_from_school_admin_but_retains_for_super_admin(self):
        request = self._create()
        self.client.force_authenticate(self.admin_a)

        # Deleting as School Admin
        response = self.client.delete(f'{self.URL}{request.pk}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # Verify it still exists in DB and has deleted_by_school=True
        request.refresh_from_db()
        self.assertTrue(request.deleted_by_school)

        # Verify School Admin cannot see it anymore in list
        response = self.client.get(self.URL)
        self.assertEqual(response.data['count'], 0)

        # Verify Super Admin can still see it in list
        self.client.force_authenticate(self.csc_admin)
        response = self.client.get(self.URL)
        self.assertEqual(response.data['count'], 1)

    def test_super_admin_delete_is_permanent_hard_delete(self):
        request = self._create()
        self.client.force_authenticate(self.csc_admin)

        # Deleting as Super Admin
        response = self.client.delete(f'{self.URL}{request.pk}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # Verify it is deleted permanently from DB
        self.assertFalse(SupportRequest.objects.filter(pk=request.pk).exists())
