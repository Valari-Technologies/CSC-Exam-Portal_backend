"""Dashboard counters: platform Total Users, the schools table, and per-teacher stats."""
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import (
    make_class,
    make_school,
    make_section,
    make_student,
    make_subject,
    make_user,
)


class PlatformStatsTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.csc_admin = make_user('csc_admin', None)
        self.school_admin = make_user('school_admin', self.school)
        self.klass = make_class(self.school)
        self.section = make_section(self.school, self.klass)
        self.client.force_authenticate(self.csc_admin)

    def stats(self):
        return self.client.get('/api/v1/schools/platform-stats/').data

    def test_total_users_counts_staff_only(self):
        """Staff means school admins + teachers. Students have their own card, and CSC
        Admins belong to no school."""
        make_user('teacher', self.school)
        make_student(self.school, self.klass, self.section)

        data = self.stats()
        # school_admin + teacher. Not the student, not the CSC Admin making the request.
        self.assertEqual(data['total_users'], 2)

    def test_total_users_equals_school_admins_plus_teachers(self):
        make_user('teacher', self.school)
        make_user('teacher', self.school)
        make_student(self.school, self.klass, self.section)

        data = self.stats()
        self.assertEqual(
            data['total_users'],
            data['total_school_admins'] + data['total_teachers'],
        )

    def test_students_do_not_move_the_number(self):
        """The whole point of the change: enrolling students must not inflate a staff
        count that an admin reads as "how many people run this platform"."""
        before = self.stats()['total_users']
        make_student(self.school, self.klass, self.section)
        make_student(self.school, self.klass, self.section)
        self.assertEqual(self.stats()['total_users'], before)

    def test_total_users_ignores_deactivated_accounts(self):
        teacher = make_user('teacher', self.school)
        before = self.stats()['total_users']

        teacher.is_active = False
        teacher.save(update_fields=['is_active'])

        self.assertEqual(self.stats()['total_users'], before - 1)

    def test_extra_csc_admins_do_not_move_the_number(self):
        before = self.stats()['total_users']
        make_user('csc_admin', None)
        self.assertEqual(self.stats()['total_users'], before)

    def test_only_csc_admin_can_read_platform_stats(self):
        self.client.force_authenticate(self.school_admin)
        response = self.client.get('/api/v1/schools/platform-stats/')
        self.assertEqual(response.status_code, 403)


class SchoolsOverviewCountTests(APITestCase):
    """Per-school counts. `user_count` is no longer shown on the admin dashboard (the
    Total Users column was removed), but the Schools list page still renders it, so it
    stays covered here.
    """

    def setUp(self):
        self.school = make_school()
        self.csc_admin = make_user('csc_admin', None)
        make_user('school_admin', self.school)
        self.klass = make_class(self.school)
        self.section = make_section(self.school, self.klass)
        self.client.force_authenticate(self.csc_admin)

    def row(self):
        response = self.client.get('/api/v1/schools/')
        return next(r for r in response.data['results'] if r['id'] == self.school.pk)

    def test_counts_active_users(self):
        make_user('teacher', self.school)
        make_student(self.school, self.klass, self.section)

        row = self.row()
        self.assertEqual(row['user_count'], 3)
        self.assertEqual(row['teachers_count'], 1)
        self.assertEqual(row['students_count'], 1)

    def test_deactivated_users_are_not_counted(self):
        teacher = make_user('teacher', self.school)
        teacher.is_active = False
        teacher.save(update_fields=['is_active'])

        row = self.row()
        self.assertEqual(row['teachers_count'], 0)
        self.assertEqual(row['user_count'], 1)  # the school admin only

    def test_user_count_is_everyone_in_the_school(self):
        """Deliberately NOT the same measure as the platform Total Users card, which is
        staff-only. This column counts students too — it is the school's whole roll.
        """
        make_user('teacher', self.school)
        make_student(self.school, self.klass, self.section)

        row = self.row()
        self.assertEqual(row['user_count'], 3)  # school admin + teacher + student


class TeacherSubjectStatsTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school)
        self.other_class = make_class(self.school, numeric_value=9)
        self.section = make_section(self.school, self.klass)
        self.teacher_user = make_user('teacher', self.school)
        self.profile = TeacherProfile.objects.create(
            user=self.teacher_user, school=self.school, teacher_id='TSTTR_001',
        )
        self.client.force_authenticate(self.teacher_user)

    def stats(self):
        return self.client.get('/api/v1/teachers/my-stats/').data

    def assign(self, subject=None, school_class=None, section=None):
        return TeacherAssignment.objects.create(
            teacher=self.profile,
            subject=subject,
            school_class=school_class or self.klass,
            section=section,
        )

    def test_counts_distinct_subjects_this_teacher_teaches(self):
        maths = make_subject(self.school, self.klass, name='Maths')
        science = make_subject(self.school, self.klass, name='Science')
        self.assign(subject=maths, section=self.section)
        self.assign(subject=science, section=self.section)

        self.assertEqual(self.stats()['assigned_subjects'], 2)

    def test_the_same_subject_twice_counts_once(self):
        maths = make_subject(self.school, self.klass, name='Maths')
        other_section = make_section(self.school, self.klass)
        self.assign(subject=maths, section=self.section)
        self.assign(subject=maths, section=other_section)

        self.assertEqual(self.stats()['assigned_subjects'], 1)

    def test_class_only_assignments_contribute_no_subject(self):
        """An assignment made before any subject was chosen carries none — counting it
        would report a subject the teacher does not teach."""
        self.assign(section=self.section)

        data = self.stats()
        self.assertEqual(data['assigned_subjects'], 0)
        self.assertEqual(data['assigned_classes'], 1)

    def test_is_not_the_schools_subject_total(self):
        """The bug: the card showed every subject in the school."""
        make_subject(self.school, self.klass, name='Maths')
        make_subject(self.school, self.klass, name='Science')
        make_subject(self.school, self.other_class, name='History')
        self.assign(section=self.section)  # teaches none of them

        self.assertEqual(self.stats()['assigned_subjects'], 0)

    def test_another_teachers_subjects_are_not_counted(self):
        maths = make_subject(self.school, self.klass, name='Maths')
        science = make_subject(self.school, self.klass, name='Science')
        self.assign(subject=maths, section=self.section)

        other = TeacherProfile.objects.create(
            user=make_user('teacher', self.school), school=self.school, teacher_id='TSTTR_002',
        )
        TeacherAssignment.objects.create(
            teacher=other, subject=science, school_class=self.klass, section=self.section,
        )

        self.assertEqual(self.stats()['assigned_subjects'], 1)

    def test_non_teachers_have_no_personal_stats(self):
        self.client.force_authenticate(make_user('school_admin', self.school))
        response = self.client.get('/api/v1/teachers/my-stats/')
        self.assertEqual(response.status_code, 403)
