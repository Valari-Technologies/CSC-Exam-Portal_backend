"""Chapter creation no longer takes an order_number, and the list is in creation order.

The New Chapter form dropped the Order Number field, so the API must accept a create
without it and auto-assign the next value per subject (keeps the "Ch.N" label sequential).
The chapter list is ordered by creation (oldest first, newest last), stable across refreshes.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.academics.models import Chapter

from .factories import make_class, make_school, make_subject, make_user


class ChapterOrderingTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.school_class = make_class(self.school)
        self.subject = make_subject(self.school, self.school_class)
        self.admin = make_user('school_admin', self.school)
        self.client.force_authenticate(self.admin)

    def _create(self, name):
        return self.client.post(
            '/api/v1/chapters/',
            {'subject': self.subject.id, 'name': name, 'description': '', 'is_active': True},
        )

    def test_create_without_order_number_succeeds(self):
        resp = self._create('Introduction')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_order_number_auto_increments_per_subject(self):
        first = self._create('First')
        second = self._create('Second')

        self.assertEqual(first.data['order_number'], 1)
        self.assertEqual(second.data['order_number'], 2)

    def test_order_number_is_scoped_to_the_subject(self):
        other_subject = make_subject(self.school, self.school_class)
        self._create('First')  # subject -> order 1
        resp = self.client.post(
            '/api/v1/chapters/',
            {'subject': other_subject.id, 'name': 'First', 'description': '', 'is_active': True},
        )

        # A different subject starts its own numbering at 1.
        self.assertEqual(resp.data['order_number'], 1)

    def test_chapters_listed_in_creation_order(self):
        """Order is by creation, not alphabetical by name."""
        first = Chapter.objects.create(subject=self.subject, name='Zebra')
        second = Chapter.objects.create(subject=self.subject, name='Apple')
        third = Chapter.objects.create(subject=self.subject, name='Mango')

        resp = self.client.get('/api/v1/chapters/?page_size=200')

        ids = [c['id'] for c in resp.data['results']]
        self.assertEqual(ids, [first.id, second.id, third.id])
