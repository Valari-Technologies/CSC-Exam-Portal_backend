"""The subject list must honour ?page_size so callers can fetch every subject.

Regression guard: SubjectViewSet used the stock pagination that ignores page_size and
capped every response at 20. With ~5 subjects per class across 10 classes that returned
only the first few classes' subjects, so the New Chapter dialog showed no subjects for
the higher classes. SubjectViewSet now uses LargePagination (page_size up to 500).
"""
from rest_framework import status
from rest_framework.test import APITestCase

from .factories import make_class, make_school, make_subject, make_user


class SubjectListPaginationTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        # Ten classes with five subjects each — 50 subjects, well past the old cap of 20.
        for grade in range(1, 11):
            school_class = make_class(self.school, numeric_value=grade)
            for _ in range(5):
                make_subject(self.school, school_class)
        self.admin = make_user('school_admin', self.school)

    def test_large_page_size_returns_all_subjects(self):
        self.client.force_authenticate(self.admin)

        resp = self.client.get('/api/v1/subjects/?page_size=200')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 50)
        self.assertEqual(len(resp.data['results']), 50)

    def test_subjects_span_every_class(self):
        """A subject is returned for all ten classes, not just the first few."""
        self.client.force_authenticate(self.admin)

        resp = self.client.get('/api/v1/subjects/?page_size=200')

        class_ids = {s['school_class'] for s in resp.data['results']}
        self.assertEqual(len(class_ids), 10)

    def test_subjects_returned_in_creation_order(self):
        """Order is by creation (newest last), not alphabetical by name."""
        school = make_school()
        school_class = make_class(school, numeric_value=1)
        admin = make_user('school_admin', school)
        # Deliberately created in a non-alphabetical name sequence.
        first = make_subject(school, school_class, name='Zoology')
        second = make_subject(school, school_class, name='Algebra')
        third = make_subject(school, school_class, name='Music')
        self.client.force_authenticate(admin)

        resp = self.client.get('/api/v1/subjects/?page_size=200')

        ids = [s['id'] for s in resp.data['results']]
        self.assertEqual(ids, [first.id, second.id, third.id])
