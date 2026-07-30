"""Teacher dashboard stats — the My Classes card counts the TEACHER's own assignments.

The rule: a teacher assigned Class 8-A, Class 9-B and Class 10-C sees 3 — the number of
class/section assignments, never the school's class total. Counted on the distinct
(class, section) pair, so two sections of Class 8 are 2 but a duplicate pair is still 1.
Every teacher sees only their own figure.
"""
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import make_class, make_school, make_section, make_subject, make_user

User = get_user_model()


class TeacherMyStatsTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.teacher_user = make_user(User.Role.TEACHER, self.school)
        self.teacher = TeacherProfile.objects.create(
            user=self.teacher_user, school=self.school, teacher_id='TSTTR_001',
        )
        self.client.force_authenticate(self.teacher_user)
        self.url = reverse('teacher-my-stats')

    def _assign(self, teacher, school_class, subject, section=None):
        return TeacherAssignment.objects.create(
            teacher=teacher,
            subject=subject,
            school_class=school_class,
            section=section,
            academic_year='2025-26',
            assigned_by=self.teacher_user,
        )

    def test_counts_the_teachers_own_class_section_assignments(self):
        """Karthick: Class 8-A, Class 9-B, Class 10-C -> the card shows 3."""
        for numeric, section_name in ((8, 'A'), (9, 'B'), (10, 'C')):
            school_class = make_class(self.school, numeric)
            section = make_section(self.school, school_class, name=section_name)
            self._assign(self.teacher, school_class, None, section)

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['assigned_classes'], 3)

    def test_does_not_report_the_schools_class_total(self):
        # The school has many classes; the teacher is assigned to one.
        for numeric in range(1, 9):
            make_class(self.school, numeric)
        only = make_class(self.school, 9)
        self._assign(self.teacher, only, None, make_section(self.school, only, name='A'))

        resp = self.client.get(self.url)

        self.assertEqual(resp.data['assigned_classes'], 1)

    def test_two_sections_of_one_class_count_as_two(self):
        """Each class/section assignment counts.

        Class 8-A and Class 8-B are two assignments, so the card shows 2. This reverses the
        earlier distinct-class rule: assignments are now made per class AND section, and the
        card reports the number of class assignments.
        """
        school_class = make_class(self.school, 8)
        section_a = make_section(self.school, school_class, name='A')
        section_b = make_section(self.school, school_class, name='B')
        self._assign(self.teacher, school_class, None, section_a)
        self._assign(self.teacher, school_class, None, section_b)

        resp = self.client.get(self.url)

        self.assertEqual(resp.data['assigned_classes'], 2)

    def test_the_same_class_section_pair_is_never_double_counted(self):
        """Distinct on the pair: a subject-bound row must not inflate the card."""
        school_class = make_class(self.school, 8)
        section_a = make_section(self.school, school_class, name='A')
        subject = make_subject(self.school, school_class)
        # Same class/section, once with a subject and once without.
        self._assign(self.teacher, school_class, None, section_a)
        self._assign(self.teacher, school_class, subject, section_a)

        resp = self.client.get(self.url)

        self.assertEqual(resp.data['assigned_classes'], 1)

    def test_another_teachers_assignments_are_not_counted(self):
        """Each teacher sees only their own assignments, regardless of anyone else's."""
        mine = make_class(self.school, 8)
        self._assign(self.teacher, mine, None, make_section(self.school, mine, name='A'))

        other_user = make_user(User.Role.TEACHER, self.school)
        other = TeacherProfile.objects.create(
            user=other_user, school=self.school, teacher_id='TSTTR_002',
        )
        for numeric in (9, 10, 6):
            cls = make_class(self.school, numeric)
            self._assign(other, cls, None, make_section(self.school, cls, name='A'))

        mine_resp = self.client.get(self.url)
        self.assertEqual(mine_resp.data['assigned_classes'], 1)

        # And the other teacher sees their own three.
        self.client.force_authenticate(other_user)
        other_resp = self.client.get(self.url)
        self.assertEqual(other_resp.data['assigned_classes'], 3)

    def test_teacher_with_no_assignments_sees_zero(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['assigned_classes'], 0)

    def test_teacher_in_another_school_is_isolated(self):
        other_school = make_school()
        other_user = make_user(User.Role.TEACHER, other_school)
        other = TeacherProfile.objects.create(
            user=other_user, school=other_school, teacher_id='OTHTR_001',
        )
        cls = make_class(other_school, 8)
        self._assign(other, cls, None, make_section(other_school, cls, name='A'))

        resp = self.client.get(self.url)

        self.assertEqual(resp.data['assigned_classes'], 0)

    def test_non_teacher_roles_are_rejected(self):
        for role in (User.Role.SCHOOL_ADMIN, User.Role.CSC_ADMIN):
            self.client.force_authenticate(
                make_user(role, self.school if role == User.Role.SCHOOL_ADMIN else None),
            )
            resp = self.client.get(self.url)
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AddTeacherWithAssignmentsTests(APITestCase):
    """The School Admin assigns classes/sections while creating the teacher."""

    def setUp(self):
        self.school = make_school(code='KAR_001', name='Karapettai Nadar Hr.Sec.School')
        self.school_admin = make_user(User.Role.SCHOOL_ADMIN, self.school)
        self.client.force_authenticate(self.school_admin)
        self.url = reverse('teacher-list')

        # Karthick's classes: 8-A, 9-B, 10-C.
        self.classes = {n: make_class(self.school, n) for n in (8, 9, 10)}
        self.sections = {
            8: make_section(self.school, self.classes[8], name='A'),
            9: make_section(self.school, self.classes[9], name='B'),
            10: make_section(self.school, self.classes[10], name='C'),
        }

    def payload(self, assignments=None, **extra) -> dict:
        data = {
            'email': 'karthick@school.edu',
            'full_name': 'Karthick',
            # Item 2: Add Teacher requires every profile field.
            'employee_id': 'EMP-1',
            'gender': 'male',
            'qualification': 'M.Sc',
            'joining_date': '2020-06-01',
            'assignments': assignments if assignments is not None else [],
        }
        data.update(extra)
        return data

    def karthick_assignments(self) -> list:
        return [
            {'school_class': self.classes[n].id, 'section': self.sections[n].id}
            for n in (8, 9, 10)
        ]

    def test_create_persists_the_class_assignments(self):
        resp = self.client.post(self.url, self.payload(self.karthick_assignments()), format='json')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        teacher = TeacherProfile.objects.get(pk=resp.data['id'])
        rows = TeacherAssignment.objects.filter(teacher=teacher)
        self.assertEqual(rows.count(), 3)
        self.assertEqual(
            sorted(rows.values_list('school_class__numeric_value', flat=True)), [8, 9, 10],
        )
        # No subject is chosen on the Add Teacher form.
        self.assertTrue(all(row.subject_id is None for row in rows))

    def test_assignments_show_on_the_create_response_and_detail_read(self):
        resp = self.client.post(self.url, self.payload(self.karthick_assignments()), format='json')

        self.assertEqual(len(resp.data['assignments']), 3)
        # A class-only assignment has no subject — it must not blow up serialization.
        self.assertIsNone(resp.data['assignments'][0]['subject_name'])

        detail = self.client.get(reverse('teacher-detail', args=[resp.data['id']]))
        self.assertEqual(len(detail.data['assignments']), 3)

    def test_the_new_teachers_dashboard_card_shows_three(self):
        """End to end: assigned at creation -> My Classes shows 3."""
        created = self.client.post(
            self.url, self.payload(self.karthick_assignments()), format='json',
        )
        teacher_user = TeacherProfile.objects.get(pk=created.data['id']).user

        self.client.force_authenticate(teacher_user)
        resp = self.client.get(reverse('teacher-my-stats'))

        self.assertEqual(resp.data['assigned_classes'], 3)

    def test_creating_without_assignments_still_works(self):
        resp = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            TeacherAssignment.objects.filter(teacher_id=resp.data['id']).count(), 0,
        )

    def test_assignment_without_a_section_covers_the_whole_class(self):
        resp = self.client.post(
            self.url,
            self.payload([{'school_class': self.classes[8].id, 'section': None}]),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        row = TeacherAssignment.objects.get(teacher_id=resp.data['id'])
        self.assertIsNone(row.section_id)

    def test_duplicate_class_section_is_rejected(self):
        duplicate = [
            {'school_class': self.classes[8].id, 'section': self.sections[8].id},
            {'school_class': self.classes[8].id, 'section': self.sections[8].id},
        ]
        resp = self.client.post(self.url, self.payload(duplicate), format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email='karthick@school.edu').exists())

    def test_cannot_assign_a_class_from_another_school(self):
        other_school = make_school()
        foreign = make_class(other_school, 8)

        resp = self.client.post(
            self.url,
            self.payload([{'school_class': foreign.id, 'section': None}]),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        # The whole create rolls back — no half-made teacher.
        self.assertFalse(User.objects.filter(email='karthick@school.edu').exists())
        self.assertFalse(TeacherProfile.objects.filter(school=other_school).exists())

    def test_section_must_belong_to_the_chosen_class(self):
        resp = self.client.post(
            self.url,
            # Section C belongs to Class 10, not Class 8.
            self.payload([{'school_class': self.classes[8].id, 'section': self.sections[10].id}]),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assignments_are_rejected_on_update(self):
        created = self.client.post(self.url, self.payload(), format='json')

        resp = self.client.patch(
            reverse('teacher-detail', args=[created.data['id']]),
            {'assignments': self.karthick_assignments()},
            format='json',
        )

        # Silently ignoring the admin's edit would be worse than refusing it.
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            TeacherAssignment.objects.filter(teacher_id=created.data['id']).count(), 0,
        )

    def test_setup_link_still_works_alongside_assignments(self):
        """Assignments must not disturb the password-setup provisioning."""
        resp = self.client.post(self.url, self.payload(self.karthick_assignments()), format='json')

        self.assertIn('/setup-password', resp.data['setup_link'])
        self.assertEqual(resp.data['teacher_id'], 'KAR_TR_001')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TeacherSubjectAndAcademicYearTests(APITestCase):
    """Subject (per class) and Academic Year, chosen on the Add Teacher form."""

    def setUp(self):
        self.school = make_school(code='KAR_001', name='Karapettai Nadar Hr.Sec.School')
        self.school_admin = make_user(User.Role.SCHOOL_ADMIN, self.school)
        self.client.force_authenticate(self.school_admin)
        self.url = reverse('teacher-list')

        self.class_8 = make_class(self.school, 8)
        self.class_9 = make_class(self.school, 9)
        self.section_a = make_section(self.school, self.class_8, name='A')
        # A Subject belongs to ONE class: 'Maths for Class 8' != 'Maths for Class 9'.
        self.maths_8 = make_subject(self.school, self.class_8, name='Mathematics')
        self.maths_9 = make_subject(self.school, self.class_9, name='Mathematics')

    def payload(self, assignments, academic_year='2025-26', **extra) -> dict:
        data = {
            'email': 'karthick@school.edu',
            'full_name': 'Karthick',
            # Item 2: Add Teacher requires every profile field.
            'employee_id': 'EMP-1',
            'gender': 'male',
            'qualification': 'M.Sc',
            'joining_date': '2020-06-01',
            'academic_year': academic_year,
            'assignments': assignments,
        }
        data.update(extra)
        return data

    def test_subject_and_academic_year_are_stored(self):
        resp = self.client.post(
            self.url,
            self.payload([{
                'school_class': self.class_8.id,
                'section': self.section_a.id,
                'subject': self.maths_8.id,
            }]),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        row = TeacherAssignment.objects.get(teacher_id=resp.data['id'])
        self.assertEqual(row.subject_id, self.maths_8.id)
        self.assertEqual(row.academic_year, '2025-26')

    def test_academic_year_is_stamped_on_every_assignment(self):
        resp = self.client.post(
            self.url,
            self.payload([
                {'school_class': self.class_8.id, 'section': None, 'subject': self.maths_8.id},
                {'school_class': self.class_9.id, 'section': None, 'subject': self.maths_9.id},
            ]),
            format='json',
        )

        years = TeacherAssignment.objects.filter(
            teacher_id=resp.data['id'],
        ).values_list('academic_year', flat=True)
        self.assertEqual(list(years), ['2025-26', '2025-26'])

    def test_subject_shows_on_reads(self):
        created = self.client.post(
            self.url,
            self.payload([{
                'school_class': self.class_8.id,
                'section': self.section_a.id,
                'subject': self.maths_8.id,
            }]),
            format='json',
        )

        detail = self.client.get(reverse('teacher-detail', args=[created.data['id']]))
        assignment = detail.data['assignments'][0]
        self.assertEqual(assignment['subject_name'], 'Mathematics')
        self.assertEqual(assignment['academic_year'], '2025-26')

    def test_subject_must_belong_to_the_assigned_class(self):
        """Maths-for-Class-9 cannot be attached to a Class 8 assignment."""
        resp = self.client.post(
            self.url,
            self.payload([{
                'school_class': self.class_8.id,
                'section': self.section_a.id,
                'subject': self.maths_9.id,
            }]),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email='karthick@school.edu').exists())

    def test_subject_from_another_school_is_rejected(self):
        other_school = make_school()
        other_class = make_class(other_school, 8)
        foreign_subject = make_subject(other_school, other_class, name='Physics')

        resp = self.client.post(
            self.url,
            self.payload([{
                'school_class': self.class_8.id, 'section': None, 'subject': foreign_subject.id,
            }]),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_subject_stays_optional(self):
        """A school with no subjects must still be able to assign classes."""
        resp = self.client.post(
            self.url,
            self.payload([{'school_class': self.class_8.id, 'section': self.section_a.id}]),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(TeacherAssignment.objects.get(teacher_id=resp.data['id']).subject_id)

    def test_academic_year_stays_optional(self):
        resp = self.client.post(
            self.url,
            self.payload([{'school_class': self.class_8.id, 'section': None}], academic_year=''),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        row = TeacherAssignment.objects.get(teacher_id=resp.data['id'])
        self.assertEqual(row.academic_year, '')

    def test_two_subjects_for_one_class_section_are_both_allowed(self):
        """Teaching Maths and Science to 8-A is two assignments, not a duplicate."""
        science_8 = make_subject(self.school, self.class_8, name='Science')

        resp = self.client.post(
            self.url,
            self.payload([
                {'school_class': self.class_8.id, 'section': self.section_a.id,
                 'subject': self.maths_8.id},
                {'school_class': self.class_8.id, 'section': self.section_a.id,
                 'subject': science_8.id},
            ]),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(TeacherAssignment.objects.filter(teacher_id=resp.data['id']).count(), 2)

    def test_the_same_subject_twice_is_still_a_duplicate(self):
        resp = self.client.post(
            self.url,
            self.payload([
                {'school_class': self.class_8.id, 'section': self.section_a.id,
                 'subject': self.maths_8.id},
                {'school_class': self.class_8.id, 'section': self.section_a.id,
                 'subject': self.maths_8.id},
            ]),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_my_classes_counts_the_class_section_pair_once_across_subjects(self):
        """Two subjects on 8-A is one class/section, so the card shows 1."""
        science_8 = make_subject(self.school, self.class_8, name='Science')
        created = self.client.post(
            self.url,
            self.payload([
                {'school_class': self.class_8.id, 'section': self.section_a.id,
                 'subject': self.maths_8.id},
                {'school_class': self.class_8.id, 'section': self.section_a.id,
                 'subject': science_8.id},
            ]),
            format='json',
        )
        teacher_user = TeacherProfile.objects.get(pk=created.data['id']).user

        self.client.force_authenticate(teacher_user)
        resp = self.client.get(reverse('teacher-my-stats'))

        self.assertEqual(resp.data['assigned_classes'], 1)

    def test_academic_year_is_rejected_on_update(self):
        created = self.client.post(
            self.url, self.payload([], academic_year='2025-26'), format='json',
        )

        resp = self.client.patch(
            reverse('teacher-detail', args=[created.data['id']]),
            {'academic_year': '2026-27'},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
