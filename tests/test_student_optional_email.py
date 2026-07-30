"""Creating a student without an email address.

Students sign in at /studentlogin with a Student ID, so an email is contact information
for them rather than a credential — and plenty of school students have none.

The trap running through all of this: a blank email must be stored as NULL, never ''.
`users.email` is unique, and Postgres does not compare NULLs, so any number of NULL rows
coexist while a second '' would violate the index. Every write path has to fold blank to
None, or the *second* email-less student fails with a constraint error.
"""
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework import status
from rest_framework.test import APITestCase

from apps.students.models import StudentProfile

from .factories import make_class, make_school, make_section, make_user

User = get_user_model()


class CreateStudentWithoutEmailTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.admin = make_user('school_admin', self.school)
        self.klass = make_class(self.school)
        self.section = make_section(self.school, self.klass)
        self.client.force_authenticate(self.admin)

    def create(self, **overrides):
        payload = {
            'full_name': 'Ana Roy',
            'school_class': self.klass.pk,
            'section': self.section.pk,
            'roll_number': '1',
            # Item 3: every field except email is required on create.
            'admission_number': 'ADM1',
            'date_of_birth': '2015-01-01',
            'gender': 'female',
            'parent_name': 'Roy',
            'parent_phone': '9000000000',
        }
        payload.update(overrides)
        return self.client.post('/api/v1/students/', payload, format='json')

    def test_a_student_can_be_created_with_no_email_field_at_all(self):
        response = self.create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(User.objects.get(full_name='Ana Roy').email)

    def test_a_blank_email_is_stored_as_null(self):
        response = self.create(email='')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(User.objects.get(full_name='Ana Roy').email)

    def test_an_explicit_null_email_is_accepted(self):
        response = self.create(email=None)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(User.objects.get(full_name='Ana Roy').email)

    def test_two_students_without_email_can_coexist(self):
        """The collision this whole change turns on."""
        first = self.create(email='')
        second = self.create(full_name='Bala Raj', roll_number='2', email='')

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.data)
        self.assertEqual(StudentProfile.objects.count(), 2)
        self.assertEqual(User.objects.filter(role='student', email__isnull=True).count(), 2)

    def test_an_email_less_student_still_gets_working_credentials(self):
        response = self.create(email='')
        student_id = response.data['student_id']
        password = response.data['initial_password']

        login = self.client.post('/api/v1/auth/student/login/', {
            'student_id': student_id,
            'password': password,
        })
        self.assertEqual(login.status_code, status.HTTP_200_OK, login.data)

    def test_a_supplied_email_is_still_saved(self):
        self.create(email='ana@example.com')
        self.assertEqual(User.objects.get(full_name='Ana Roy').email, 'ana@example.com')

    def test_a_supplied_email_must_still_be_unique(self):
        self.create(email='ana@example.com')
        response = self.create(full_name='Bala Raj', roll_number='2', email='ana@example.com')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)

    def test_a_malformed_email_is_still_rejected(self):
        """Optional does not mean unvalidated."""
        response = self.create(email='not-an-email')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)


class EditingAStudentsEmailTests(APITestCase):
    """Editing must not erase an email nobody touched.

    `email` carries default=None so that create can mean "no email". DRF fills defaults
    for absent fields on a full update, so a PUT that never mentions email would
    otherwise deserialize to None and wipe a real address.
    """

    def setUp(self):
        self.school = make_school()
        self.admin = make_user('school_admin', self.school)
        self.klass = make_class(self.school)
        self.section = make_section(self.school, self.klass)
        self.client.force_authenticate(self.admin)

        created = self.client.post('/api/v1/students/', {
            'full_name': 'Ana Roy',
            'email': 'ana@example.com',
            'school_class': self.klass.pk,
            'section': self.section.pk,
            'roll_number': '1',
            'admission_number': 'ADM1',
            'date_of_birth': '2015-01-01',
            'gender': 'female',
            'parent_name': 'Roy',
            'parent_phone': '9000000000',
        }, format='json')
        self.student_pk = created.data['id']
        self.user = User.objects.get(full_name='Ana Roy')

    def patch(self, **payload):
        return self.client.patch(
            f'/api/v1/students/{self.student_pk}/', payload, format='json',
        )

    def test_editing_another_field_leaves_the_email_alone(self):
        response = self.patch(roll_number='7')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'ana@example.com')

    def test_a_full_update_without_an_email_key_does_not_wipe_it(self):
        """The exact shape that would silently erase the address."""
        response = self.client.put(f'/api/v1/students/{self.student_pk}/', {
            'full_name': 'Ana Roy',
            'school_class': self.klass.pk,
            'section': self.section.pk,
            'roll_number': '1',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'ana@example.com')

    def test_an_email_can_still_be_changed(self):
        self.patch(email='new@example.com')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.com')

    def test_explicitly_clearing_the_email_still_works(self):
        """Sending a blank email is a deliberate "remove it", not an omission."""
        self.patch(email='')
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email)

    def test_an_email_can_be_added_to_a_student_who_had_none(self):
        created = self.client.post('/api/v1/students/', {
            'full_name': 'Bala Raj',
            'school_class': self.klass.pk,
            'section': self.section.pk,
            'roll_number': '2',
            'admission_number': 'ADM2',
            'date_of_birth': '2015-01-01',
            'gender': 'male',
            'parent_name': 'Raj',
            'parent_phone': '9000000001',
        }, format='json')
        self.client.patch(
            f"/api/v1/students/{created.data['id']}/",
            {'email': 'bala@example.com'}, format='json',
        )
        self.assertEqual(User.objects.get(full_name='Bala Raj').email, 'bala@example.com')


class EmailUniquenessAtTheDatabaseTests(APITestCase):
    """Pins the two halves of the NULL-uniqueness rule the serializers depend on."""

    def setUp(self):
        self.school = make_school()

    def test_many_users_may_have_a_null_email(self):
        for n in range(3):
            User.objects.create_user(
                email=None, password='x', full_name=f'No Email {n}',
                role=User.Role.STUDENT, school=self.school, student_id=f'X-000{n}',
            )
        self.assertEqual(User.objects.filter(email__isnull=True).count(), 3)

    def test_a_duplicate_real_email_is_still_a_database_error(self):
        User.objects.create_user(
            email='dup@example.com', password='x', full_name='One',
            role=User.Role.STUDENT, school=self.school, student_id='X-0001',
        )
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                email='dup@example.com', password='x', full_name='Two',
                role=User.Role.STUDENT, school=self.school, student_id='X-0002',
            )

    def test_a_superuser_still_requires_an_email(self):
        """It is their USERNAME_FIELD — an email-less superuser could never sign in."""
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email='', password='x', full_name='Root')
