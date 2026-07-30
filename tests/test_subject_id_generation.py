"""The Subject ID (Subject.code) is server-generated, unique per school, and kept in sync.

Format: <school 2-letter prefix>_<subject 3-letter abbreviation>_<2-digit class>, e.g.
KA_MAT_10. Users never supply it — the field is read-only. It is generated on create and
regenerated when the subject's name or class changes, but left alone on any other edit.
The test school factory issues codes like 'SCH0042', so the school prefix here is 'SC'.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.academics.models import Subject
from apps.academics.services import generate_subject_id, subject_letter_prefix

from .factories import make_class, make_school, make_subject, make_user


class SubjectIdGenerationTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.class_10 = make_class(self.school, numeric_value=10)
        self.class_5 = make_class(self.school, numeric_value=5)
        self.admin = make_user('school_admin', self.school)
        self.client.force_authenticate(self.admin)

    def _create(self, name, school_class, **extra):
        payload = {
            'school_class': school_class.id,
            'name': name,
            'description': '',
            'is_active': True,
            **extra,
        }
        return self.client.post('/api/v1/subjects/', payload)

    def test_standard_subject_prefixes(self):
        self.assertEqual(subject_letter_prefix('Mathematics'), 'MAT')
        self.assertEqual(subject_letter_prefix('English'), 'ENG')
        self.assertEqual(subject_letter_prefix('Tamil'), 'TAM')
        self.assertEqual(subject_letter_prefix('Science'), 'SCI')
        self.assertEqual(subject_letter_prefix('Social Science'), 'SOC')

    def test_unknown_subject_falls_back_to_first_three_letters(self):
        self.assertEqual(subject_letter_prefix('Computer'), 'COM')
        self.assertEqual(subject_letter_prefix('Hi'), 'HIX')

    def test_create_generates_expected_id(self):
        resp = self._create('Mathematics', self.class_10)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['code'], 'SC_MAT_10')

    def test_class_number_is_zero_padded(self):
        resp = self._create('English', self.class_5)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['code'], 'SC_ENG_05')

    def test_code_is_read_only_and_ignores_client_value(self):
        resp = self._create('Science', self.class_10, code='HACK_ME_99')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['code'], 'SC_SCI_10')

    def test_collision_gets_numeric_suffix(self):
        """Two different names collapsing to the same abbreviation in one class stay unique."""
        first = self._create('Physics', self.class_10)
        second = self._create('Physiology', self.class_10)

        self.assertEqual(first.data['code'], 'SC_PHY_10')
        self.assertEqual(second.data['code'], 'SC_PHY_10_2')

    def test_rename_regenerates_id(self):
        resp = self._create('Mathematics', self.class_10)
        subject_id = resp.data['id']

        upd = self.client.patch(f'/api/v1/subjects/{subject_id}/', {'name': 'Science'})

        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertEqual(upd.data['code'], 'SC_SCI_10')

    def test_class_change_regenerates_id(self):
        resp = self._create('Mathematics', self.class_10)
        subject_id = resp.data['id']

        upd = self.client.patch(
            f'/api/v1/subjects/{subject_id}/', {'school_class': self.class_5.id},
        )

        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertEqual(upd.data['code'], 'SC_MAT_05')

    def test_status_only_patch_keeps_id(self):
        resp = self._create('Mathematics', self.class_10)
        subject_id = resp.data['id']
        original = resp.data['code']

        upd = self.client.patch(f'/api/v1/subjects/{subject_id}/', {'is_active': False})

        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertEqual(upd.data['code'], original)
        self.assertFalse(upd.data['is_active'])

    def test_unique_within_school_but_independent_across_schools(self):
        """The same Subject ID can exist in two different schools (uniqueness is per-school)."""
        first = self._create('Mathematics', self.class_10)
        self.assertEqual(first.data['code'], 'SC_MAT_10')

        # A second school whose code also yields the 'SC' prefix.
        other_school = make_school(code='SCX9001')
        other_class = make_class(other_school, numeric_value=10)
        other_admin = make_user('school_admin', other_school)
        self.client.force_authenticate(other_admin)

        resp = self._create('Mathematics', other_class)
        self.assertEqual(resp.data['code'], 'SC_MAT_10')
        self.assertEqual(
            Subject.objects.filter(code='SC_MAT_10').count(), 2,
        )

    def test_generate_subject_id_helper_excludes_self(self):
        """Regenerating for an existing subject must not treat its own code as a collision."""
        subject = make_subject(self.school, self.class_10, name='Mathematics')
        subject.code = generate_subject_id(self.school, 'Mathematics', 10)
        subject.save(update_fields=['code'])

        again = generate_subject_id(self.school, 'Mathematics', 10, exclude_pk=subject.pk)
        self.assertEqual(again, 'SC_MAT_10')
