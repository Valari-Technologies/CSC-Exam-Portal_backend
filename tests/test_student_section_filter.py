"""Filtering the student list by section LETTER.

The Students page gained a Section filter alongside its existing Class filter. It offers a
fixed A-F list rather than the sections of the selected class, so it stays usable while
"All classes" is selected — which means the API has to filter on the section's NAME, not
on a section PK. `section` (the PK filter) still exists and is untouched; `section__name`
is the addition.

The rule that matters here: the filter NARROWS, it never widens. A teacher is already
restricted to the class/sections a School Admin assigned them (see
test_student_teacher_edit_scope), and asking for a letter outside that scope must return
nothing rather than reaching across it.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.teachers.models import TeacherAssignment, TeacherProfile

from .factories import make_class, make_school, make_section, make_student, make_user


class SectionNameFilterTests(APITestCase):
    """A School Admin filtering their own school's students."""

    def setUp(self):
        self.school = make_school()
        self.admin = make_user('school_admin', self.school)

        self.class_9 = make_class(self.school, numeric_value=9)
        self.class_10 = make_class(self.school, numeric_value=10)

        # Same letters under two different classes: these are distinct Section rows, which
        # is exactly why a PK filter cannot answer "every Section A student".
        self.nine_a = make_section(self.school, self.class_9, name='A')
        self.nine_b = make_section(self.school, self.class_9, name='B')
        self.ten_a = make_section(self.school, self.class_10, name='A')
        self.ten_b = make_section(self.school, self.class_10, name='B')

        self.s9a = make_student(self.school, self.class_9, self.nine_a)
        self.s9b = make_student(self.school, self.class_9, self.nine_b)
        self.s10a = make_student(self.school, self.class_10, self.ten_a)
        self.s10b = make_student(self.school, self.class_10, self.ten_b)

        self.client.force_authenticate(self.admin)

    def _list(self, **params):
        response = self.client.get('/api/v1/students/', params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def _emails(self, data) -> set[str]:
        return {row['user_email'] for row in data['results']}

    def test_no_section_filter_returns_everyone(self):
        self.assertEqual(self._list()['count'], 4)

    def test_a_letter_spans_classes(self):
        """The point of filtering by name: Section A of 9 AND of 10."""
        data = self._list(section__name='A')

        self.assertEqual(data['count'], 2)
        self.assertEqual(self._emails(data), {self.s9a.email, self.s10a.email})

    def test_class_and_section_together_narrow_to_one_combination(self):
        data = self._list(school_class=self.class_10.pk, section__name='A')

        self.assertEqual(data['count'], 1)
        self.assertEqual(self._emails(data), {self.s10a.email})

    def test_class_filter_alone_is_unaffected(self):
        """The pre-existing filter still behaves as it did."""
        self.assertEqual(self._list(school_class=self.class_9.pk)['count'], 2)

    def test_section_pk_filter_still_works(self):
        """`section` was not replaced by `section__name` — both are supported."""
        data = self._list(section=self.ten_b.pk)

        self.assertEqual(data['count'], 1)
        self.assertEqual(self._emails(data), {self.s10b.email})

    def test_a_letter_nobody_has_returns_an_empty_page_not_an_error(self):
        """A class need not have all six sections — a real school is missing an F."""
        data = self._list(section__name='F')

        self.assertEqual(data['count'], 0)
        self.assertEqual(data['results'], [])

    def test_an_unknown_letter_returns_nothing(self):
        self.assertEqual(self._list(section__name='Z')['count'], 0)

    def test_the_filter_is_exact_not_a_prefix_match(self):
        """'A' must not also match a legacy name like 'A(a)'."""
        odd = make_section(self.school, self.class_9, name='A(a)')
        make_student(self.school, self.class_9, odd)

        data = self._list(section__name='A')

        self.assertEqual(data['count'], 2)
        self.assertEqual(self._emails(data), {self.s9a.email, self.s10a.email})

    def test_an_empty_value_is_ignored_rather_than_matching_nothing(self):
        """The UI sends no param for "All sections", but a stray '' must not blank the list."""
        self.assertEqual(self._list(section__name='')['count'], 4)


class SectionFilterDoesNotEscapeScopeTests(APITestCase):
    """The filter narrows what a user may already see. It never widens it."""

    def setUp(self):
        self.school = make_school()
        self.other_school = make_school()

        self.klass = make_class(self.school, numeric_value=10)
        self.sec_a = make_section(self.school, self.klass, name='A')
        self.sec_b = make_section(self.school, self.klass, name='B')

        self.mine_a = make_student(self.school, self.klass, self.sec_a)
        self.mine_b = make_student(self.school, self.klass, self.sec_b)

        # Another school, same class number and same letters.
        other_class = make_class(self.other_school, numeric_value=10)
        other_a = make_section(self.other_school, other_class, name='A')
        self.theirs = make_student(self.other_school, other_class, other_a)

        self.admin = make_user('school_admin', self.school)

        # A teacher assigned Section A only.
        teacher_user = make_user('teacher', self.school)
        self.teacher_profile = TeacherProfile.objects.create(
            user=teacher_user, school=self.school, teacher_id='X_TR_001',
        )
        TeacherAssignment.objects.create(
            teacher=self.teacher_profile,
            school_class=self.klass,
            section=self.sec_a,
        )
        self.teacher = teacher_user

    def _emails(self, response) -> set[str]:
        return {row['user_email'] for row in response.data['results']}

    def test_school_admin_filtering_by_letter_never_sees_another_school(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get('/api/v1/students/', {'section__name': 'A'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._emails(response), {self.mine_a.email})

    def test_teacher_filtering_by_their_own_letter_sees_their_students(self):
        self.client.force_authenticate(self.teacher)

        response = self.client.get('/api/v1/students/', {'section__name': 'A'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._emails(response), {self.mine_a.email})

    def test_teacher_filtering_by_a_letter_outside_their_scope_gets_nothing(self):
        """Section B exists and they can name it — the scope filter still wins."""
        self.client.force_authenticate(self.teacher)

        response = self.client.get('/api/v1/students/', {'section__name': 'B'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_teacher_with_no_filter_still_sees_only_their_section(self):
        self.client.force_authenticate(self.teacher)

        response = self.client.get('/api/v1/students/')

        self.assertEqual(self._emails(response), {self.mine_a.email})
