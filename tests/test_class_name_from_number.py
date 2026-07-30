"""A class is identified by its grade number; the stored name is derived from it.

The New Class form no longer collects a free-text name — the API accepts a create/update
without one and sets `name = str(numeric_value)`. A duplicate grade within a school is
rejected with a clean 400 (not an IntegrityError 500).
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.academics.models import Class

from .factories import make_class, make_school, make_user


class ClassNameFromNumberTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.admin = make_user('school_admin', self.school)
        self.client.force_authenticate(self.admin)

    def test_create_without_name_derives_name_from_number(self):
        resp = self.client.post(
            '/api/v1/classes/', {'numeric_value': 5, 'is_active': True},
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], '5')

    def test_duplicate_grade_is_rejected_cleanly(self):
        make_class(self.school, numeric_value=5)  # name '5' already taken

        resp = self.client.post(
            '/api/v1/classes/', {'numeric_value': 5, 'is_active': True},
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('numeric_value', resp.data)

    def test_update_number_updates_name(self):
        cls = make_class(self.school, numeric_value=5)

        resp = self.client.put(
            f'/api/v1/classes/{cls.id}/', {'numeric_value': 6, 'is_active': True},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], '6')
        cls.refresh_from_db()
        self.assertEqual(cls.name, '6')

    def test_status_only_patch_keeps_name(self):
        """A PATCH that omits numeric_value must not error or change the name."""
        cls = make_class(self.school, numeric_value=7)

        resp = self.client.patch(f'/api/v1/classes/{cls.id}/', {'is_active': False})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        cls.refresh_from_db()
        self.assertEqual(cls.name, '7')
        self.assertFalse(cls.is_active)

    def test_out_of_range_grade_still_rejected(self):
        resp = self.client.post(
            '/api/v1/classes/', {'numeric_value': 11, 'is_active': True},
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Class.objects.filter(school=self.school, numeric_value=11).exists())
