"""Gender on the Add Teacher flow: stored, returned, validated, and (now) required.

Item 2 made every Add Teacher field required, so the base create payload below carries the
other required fields (employee_id, qualification, joining_date) and gender is supplied
per-test — letting each test isolate gender's behaviour.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherProfile

from .factories import make_school, make_user


class TeacherGenderTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.admin = make_user('school_admin', self.school)
        self.client.force_authenticate(self.admin)

    def create_teacher(self, **extra):
        payload = {
            'email': 'new.teacher@example.com',
            'full_name': 'New Teacher',
            # The other now-required fields (item 2); gender is passed per test.
            'employee_id': 'EMP1',
            'qualification': 'M.Sc',
            'joining_date': '2020-06-01',
        }
        payload.update(extra)
        return self.client.post('/api/v1/teachers/', payload, format='json')

    def test_gender_is_saved_and_returned_on_create(self):
        response = self.create_teacher(gender='female')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['gender'], 'female')
        self.assertEqual(TeacherProfile.objects.get().gender, 'female')

    def test_gender_is_required_on_create(self):
        """Item 2: gender is no longer optional on Add Teacher."""
        response = self.create_teacher()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('gender', response.data)
        self.assertFalse(TeacherProfile.objects.exists())

    def test_blank_gender_is_rejected_on_create(self):
        response = self.create_teacher(gender='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('gender', response.data)

    def test_invalid_gender_is_rejected(self):
        response = self.create_teacher(gender='unknown')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('gender', response.data)
        self.assertFalse(TeacherProfile.objects.exists())

    def test_gender_can_be_updated(self):
        self.create_teacher(gender='male')
        profile = TeacherProfile.objects.get()

        response = self.client.patch(
            f'/api/v1/teachers/{profile.pk}/', {'gender': 'other'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        profile.refresh_from_db()
        self.assertEqual(profile.gender, 'other')

    def test_gender_appears_in_the_teacher_list(self):
        self.create_teacher(gender='female')
        response = self.client.get('/api/v1/teachers/')
        self.assertEqual(response.data['results'][0]['gender'], 'female')

    def test_updating_another_field_leaves_gender_alone(self):
        self.create_teacher(gender='female')
        profile = TeacherProfile.objects.get()

        self.client.patch(
            f'/api/v1/teachers/{profile.pk}/', {'qualification': 'M.Sc'}, format='json',
        )
        profile.refresh_from_db()
        self.assertEqual(profile.gender, 'female')
        self.assertEqual(profile.qualification, 'M.Sc')

    def test_existing_teachers_without_gender_still_load(self):
        """The column was added to a live table; rows predating it must read back fine."""
        profile = TeacherProfile.objects.create(
            user=make_user('teacher', self.school), school=self.school, teacher_id='OLD_001',
        )
        response = self.client.get(f'/api/v1/teachers/{profile.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['gender'], '')
