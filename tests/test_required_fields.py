"""Backend enforcement of the Add School / Add Teacher / Add Student required fields.

The frontend marks fields with an asterisk, but the asterisk is cosmetic — these tests
pin the *server* rules so a hand-crafted request can't slip a blank past the form. Item 4
makes every Add School field mandatory (Principal Name flips optional→required); item 5
keeps the core Teacher/Student fields required and the supplementary ones optional.
"""
from rest_framework.test import APITestCase

from apps.schools.serializers import SchoolSerializer
from apps.students.serializers import StudentProfileWriteSerializer
from apps.teachers.serializers import TeacherProfileWriteSerializer

from .factories import make_class, make_school, make_section


class SchoolRequiredFieldsTests(APITestCase):
    """Item 4 marks every Add School field required on the FORM (asterisk + blocked submit).

    That is a frontend rule — the item asks only that the form not submit when empty, and
    (unlike item 5) never mentions the backend. So these tests pin the boundary the OTHER
    way: the API must keep `principal_name` OPTIONAL, or existing provisioning flows and
    legacy schools with a blank principal would break. The core NOT NULL fields still fail
    fast, as they always have.
    """

    def _payload(self, **overrides) -> dict:
        payload = {
            'name': 'Green Valley School',
            'address': '1 Test Road',
            'city': 'Chennai',
            'state': 'Tamil Nadu',
            'pincode': '600001',
            'principal_name': 'A Principal',
            'official_email': 'office@greenvalley.edu',
            'contact_phone': '9000000000',
            'status': 'active',
            'admin_full_name': 'Admin One',
            'admin_email': 'admin@greenvalley.edu',
            'school_board': 'cbse',
            'school_code': '33010100101',
        }
        payload.update(overrides)
        return payload

    def test_a_complete_payload_validates(self):
        serializer = SchoolSerializer(data=self._payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_principal_name_stays_optional_at_the_api(self):
        """Item 4 is a form rule; the API must not start rejecting principal-less creates."""
        data = self._payload()
        data.pop('principal_name')
        serializer = SchoolSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_core_not_null_fields_stay_required(self):
        serializer = SchoolSerializer(data=self._payload(name='', city=''))
        self.assertFalse(serializer.is_valid())
        self.assertIn('name', serializer.errors)
        self.assertIn('city', serializer.errors)

    def test_principal_name_not_required_on_edit(self):
        """Editing a legacy school whose principal_name is blank must still be possible."""
        school = make_school()  # created with a blank principal_name
        self.assertEqual(school.principal_name, '')
        serializer = SchoolSerializer(
            instance=school, data={'status': 'inactive'}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)


class TeacherRequiredFieldsTests(APITestCase):
    """Item 2: Add Teacher now requires EVERY field, enforced on create only.

    (This supersedes the earlier split where employee_id/gender/qualification/joining_date
    were optional.) Update is left lenient so a legacy teacher stays editable — that is
    covered by test_teacher_gender.test_gender_can_be_updated.
    """

    def test_full_name_and_email_required(self):
        serializer = TeacherProfileWriteSerializer(data={})
        self.assertFalse(serializer.is_valid())
        self.assertIn('email', serializer.errors)
        self.assertIn('full_name', serializer.errors)

    def test_all_supplementary_fields_required_on_create(self):
        # email + full_name provided so object-level validation runs; the rest omitted.
        serializer = TeacherProfileWriteSerializer(
            data={'email': 'teacher@x.edu', 'full_name': 'A Teacher'},
        )
        self.assertFalse(serializer.is_valid())
        for field in ('employee_id', 'gender', 'qualification', 'joining_date'):
            self.assertIn(field, serializer.errors)

    def test_a_complete_teacher_payload_validates(self):
        serializer = TeacherProfileWriteSerializer(data={
            'email': 'teacher@x.edu',
            'full_name': 'A Teacher',
            'employee_id': 'EMP1',
            'gender': 'male',
            'qualification': 'M.Sc',
            'joining_date': '2020-06-01',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)


class StudentRequiredFieldsTests(APITestCase):
    """Item 3: Add Student requires every field EXCEPT Email, enforced on create only.

    Parent Phone is now required (reversing the earlier decision) — but only on create, so
    the students with a blank/unrecoverable number stay editable. Login credentials and
    sub_section_code remain optional.
    """

    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')

    def test_core_fields_required(self):
        serializer = StudentProfileWriteSerializer(data={})
        self.assertFalse(serializer.is_valid())
        for field in ('full_name', 'school_class', 'section', 'roll_number'):
            self.assertIn(field, serializer.errors)

    def test_all_fields_except_email_required_on_create(self):
        # Field-required ones provided so object-level validation runs; the rest omitted.
        serializer = StudentProfileWriteSerializer(data={
            'full_name': 'A Student',
            'school_class': self.klass.pk,
            'section': self.section.pk,
            'roll_number': '1',
        })
        self.assertFalse(serializer.is_valid())
        for field in ('admission_number', 'date_of_birth', 'gender', 'parent_name', 'parent_phone'):
            self.assertIn(field, serializer.errors)
        # Email is the one field that stays optional (item 3).
        self.assertNotIn('email', serializer.errors)

    def test_a_complete_student_payload_validates(self):
        serializer = StudentProfileWriteSerializer(data={
            'full_name': 'A Student',
            'school_class': self.klass.pk,
            'section': self.section.pk,
            'roll_number': '1',
            'admission_number': 'ADM1',
            'date_of_birth': '2015-01-01',
            'gender': 'male',
            'parent_name': 'Parent',
            'parent_phone': '9000000000',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
