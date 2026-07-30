"""Teachers see and edit only the class/section combinations assigned to them.

A TeacherAssignment IS the School Admin's "assign these students to this teacher" action.
Students exist independently of it — bulk import creates them with a class and section —
and stay invisible to every teacher until an assignment covers them. A teacher assigned
Class 10 Section A manages 10-A only; 10-B is another teacher's, and an unassigned class
is nobody's.

This replaced a class-level rule (2026-07-20, same day): under that rule a 10-A teacher
could also edit 10-B, and freshly imported students showed up for any teacher who happened
to share their class. Section is the boundary the School Admin actually draws.

An assignment with a NULL section still covers the whole class — that shape is valid in the
model (see `unique_whole_class_assignment_per_teacher`) and means "all sections".

`get_queryset` is the single source of truth: list, retrieve and edit all reach the database
through it, so an out-of-scope student is a 404 rather than a 403 and never appears in a
list. `_assert_can_edit` reuses the same predicate as a second gate on writes. Create,
deactivate (destroy) and hard-delete remain admin-only.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.students.models import StudentProfile
from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import (
    make_class, make_school, make_section, make_student, make_subject, make_user,
)


class TeacherStudentEditScopeTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.class_a = make_class(self.school, numeric_value=8)
        self.class_b = make_class(self.school, numeric_value=9)
        self.a1 = make_section(self.school, self.class_a, name='A')
        self.a2 = make_section(self.school, self.class_a, name='B')
        self.b1 = make_section(self.school, self.class_b, name='A')

        self.student_a1 = make_student(self.school, self.class_a, self.a1)
        self.student_a2 = make_student(self.school, self.class_a, self.a2)
        self.student_b1 = make_student(self.school, self.class_b, self.b1)

        self.teacher_user = make_user('teacher', self.school)
        self.teacher = TeacherProfile.objects.create(
            user=self.teacher_user, school=self.school, teacher_id='X_TR_001',
        )

    def _profile(self, student_user) -> StudentProfile:
        return StudentProfile.objects.get(user=student_user)

    def _assign(self, school_class, section=None, subject=None):
        TeacherAssignment.objects.create(
            teacher=self.teacher, school_class=school_class,
            section=section, subject=subject,
        )

    def _patch_name(self, student_user, name='Renamed Student'):
        profile = self._profile(student_user)
        return self.client.patch(f'/api/v1/students/{profile.pk}/', {'full_name': name})

    def _listed_ids(self) -> set:
        return {r['id'] for r in self.client.get('/api/v1/students/').data['results']}

    # --- teacher, in scope -------------------------------------------------

    def test_assigned_section_is_editable(self):
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)

        resp = self._patch_name(self.student_a1)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.student_a1.refresh_from_db()
        self.assertEqual(self.student_a1.full_name, 'Renamed Student')

    def test_null_section_assignment_covers_every_section_of_the_class(self):
        """"All sections" is a real assignment shape and must still grant the whole class."""
        self._assign(self.class_a)
        self.client.force_authenticate(self.teacher_user)

        for student in (self.student_a1, self.student_a2):
            self.assertEqual(self._patch_name(student).status_code, status.HTTP_200_OK)

    def test_two_assignments_union_their_sections(self):
        self._assign(self.class_a, self.a1)
        self._assign(self.class_b, self.b1)
        self.client.force_authenticate(self.teacher_user)

        self.assertEqual(
            self._listed_ids(),
            {self._profile(self.student_a1).pk, self._profile(self.student_b1).pk},
        )

    def test_subject_bound_assignment_grants_its_section_only(self):
        """Subject scopes questions and chapters, not students — the section still rules."""
        subject = make_subject(self.school, self.class_a)
        self._assign(self.class_a, self.a1, subject=subject)
        self.client.force_authenticate(self.teacher_user)

        self.assertEqual(self._patch_name(self.student_a1).status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._patch_name(self.student_a2).status_code, status.HTTP_404_NOT_FOUND,
        )

    def test_full_update_saves_the_changes(self):
        """The Edit Student form submits a PUT — the whole payload must round-trip."""
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)
        profile = self._profile(self.student_a1)

        resp = self.client.put(
            f'/api/v1/students/{profile.pk}/',
            {
                'full_name': 'Updated Name',
                'school_class': self.class_a.pk,
                'section': self.a1.pk,
                'roll_number': 'R-NEW',
                'parent_name': 'Parent Name',
                'parent_phone': '9000000001',
                'is_active': True,
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        profile.refresh_from_db()
        self.assertEqual(profile.roll_number, 'R-NEW')
        self.assertEqual(profile.parent_name, 'Parent Name')
        self.assertEqual(profile.user.full_name, 'Updated Name')

    def test_teacher_can_load_the_student_before_editing(self):
        """The edit form fetches the detail endpoint first; it must not 404."""
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get(f'/api/v1/students/{self._profile(self.student_a1).pk}/')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # --- teacher, out of scope ---------------------------------------------

    def test_another_section_of_the_same_class_is_out_of_reach(self):
        """The point of the rule: sharing a class is not enough, the section must match."""
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)

        listing = self._listed_ids()
        resp = self._patch_name(self.student_a2)

        self.assertNotIn(self._profile(self.student_a2).pk, listing)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.student_a2.refresh_from_db()
        self.assertNotEqual(self.student_a2.full_name, 'Renamed Student')

    def test_unassigned_class_is_out_of_reach(self):
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)

        self.assertEqual(
            self._patch_name(self.student_b1).status_code, status.HTTP_404_NOT_FOUND,
        )

    def test_teacher_with_no_assignments_sees_and_edits_nobody(self):
        """Freshly imported students are invisible until a School Admin assigns them."""
        self.client.force_authenticate(self.teacher_user)

        listing = self.client.get('/api/v1/students/')
        resp = self._patch_name(self.student_a1)

        self.assertEqual(listing.data['count'], 0)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_view_is_scoped_not_just_the_list(self):
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get(f'/api/v1/students/{self._profile(self.student_a2).pk}/')

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_is_scoped_too_not_just_patch(self):
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)
        profile = self._profile(self.student_b1)

        resp = self.client.put(
            f'/api/v1/students/{profile.pk}/',
            {
                'full_name': 'Updated Name',
                'school_class': self.class_b.pk,
                'section': self.b1.pk,
                'roll_number': 'R-NEW',
                'is_active': True,
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_class_filter_cannot_widen_scope(self):
        """?school_class= narrows an already-scoped queryset; it never reaches past it."""
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.get(f'/api/v1/students/?school_class={self.class_a.pk}')

        self.assertEqual(
            {r['id'] for r in resp.data['results']}, {self._profile(self.student_a1).pk},
        )

    def test_another_schools_student_is_not_found(self):
        """Cross-school stays a 404 from get_queryset — scope never widens tenancy."""
        other_school = make_school()
        other_class = make_class(other_school, numeric_value=8)
        other_section = make_section(other_school, other_class, name='A')
        outsider = make_student(other_school, other_class, other_section)

        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)

        self.assertEqual(self._patch_name(outsider).status_code, status.HTTP_404_NOT_FOUND)

    # --- the list matches what is editable ----------------------------------

    def test_every_listed_student_is_editable(self):
        """No dead Edit buttons: what a teacher can see is exactly what they can save."""
        self._assign(self.class_a, self.a1)
        self._assign(self.class_b)
        self.client.force_authenticate(self.teacher_user)

        listed = self._listed_ids()

        self.assertEqual(
            listed,
            {self._profile(self.student_a1).pk, self._profile(self.student_b1).pk},
        )
        for pk in listed:
            self.assertEqual(
                self.client.patch(
                    f'/api/v1/students/{pk}/', {'parent_name': 'Checked'},
                ).status_code,
                status.HTTP_200_OK,
            )

    def test_admin_list_is_not_narrowed(self):
        admin = make_user('school_admin', self.school)
        self.client.force_authenticate(admin)

        resp = self.client.get('/api/v1/students/')

        self.assertEqual(resp.data['count'], 3)

    # --- teacher, still denied elsewhere ------------------------------------

    def test_teacher_cannot_create_a_student(self):
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)

        resp = self.client.post(
            '/api/v1/students/',
            {
                'full_name': 'New Student',
                'school_class': self.class_a.pk,
                'section': self.a1.pk,
                'roll_number': 'R-999',
                'is_active': True,
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_cannot_deactivate_or_delete_a_managed_student(self):
        self._assign(self.class_a, self.a1)
        self.client.force_authenticate(self.teacher_user)
        pk = self._profile(self.student_a1).pk

        self.assertEqual(
            self.client.delete(f'/api/v1/students/{pk}/').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(f'/api/v1/students/{pk}/hard-delete/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    # --- admins unchanged ---------------------------------------------------

    def test_school_admin_manages_every_student_without_assignments(self):
        """The School Admin's own view is untouched: imports land there immediately."""
        admin = make_user('school_admin', self.school)
        self.client.force_authenticate(admin)

        for student in (self.student_a1, self.student_a2, self.student_b1):
            self.assertEqual(self._patch_name(student).status_code, status.HTTP_200_OK)

    def test_student_cannot_edit_their_own_profile(self):
        self.client.force_authenticate(self.student_a1)

        resp = self._patch_name(self.student_a1)

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
