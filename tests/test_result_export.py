"""Students can download their own results — and only their own.

Two things this pins down.

**The queryset is the permission boundary.** The list export used to require a
teacher role; it now relies on ``get_queryset``, which already limits a student to
their own PUBLISHED results. That is a safe simplification only for as long as it
stays true, so another student's row never appearing in the file is asserted here
rather than trusted.

**Rank must not leak back in through a file.** Rank was removed from every
student-facing screen; a download is a student-facing screen. The staff column
set — which still carries Rank — must stay unchanged.
"""
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.results.models import Result

from .factories import (
    make_assignment,
    make_chapter,
    make_class,
    make_question,
    make_result,
    make_school,
    make_section,
    make_session,
    make_student,
    make_subject,
    make_test,
    make_user,
)

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class ResultExportTestCase(APITestCase):
    """One published result for `student`, one for a classmate."""

    LIST_URL = '/api/v1/results/export/'

    def setUp(self):
        self.school = make_school()
        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')
        self.subject = make_subject(self.school, self.klass)
        self.chapter = make_chapter(self.subject)
        self.teacher = make_user('teacher', self.school)

        self.question = make_question(
            self.school, self.subject, self.chapter,
            correct_option='a', marks=Decimal('10'),
        )
        self.test = make_test(
            self.school, self.subject, self.klass, self.teacher,
            questions=[self.question],
        )
        self.assignment = make_assignment(self.test, self.klass, self.teacher)

        self.student = make_student(self.school, self.klass, self.section)
        self.classmate = make_student(self.school, self.klass, self.section)

        self.result = self._published(self.student, Decimal('8'))
        self.other_result = self._published(self.classmate, Decimal('3'))

    def _published(self, student, marks) -> Result:
        session = make_session(student, self.assignment, self.test)
        result = make_result(
            session, obtained=marks, total=Decimal('10'), is_published=True,
        )
        result.rank = 1
        result.save(update_fields=['rank'])
        return result

    def _download(self, url, fmt) -> object:
        response = self.client.get(url, {'file_format': fmt})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content[:400])
        return response

    @staticmethod
    def _text(response) -> str:
        return response.content.decode('utf-8')


class StudentHistoryExportTests(ResultExportTestCase):
    """The "Download Results" control on My Results."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.student)

    def test_csv_download(self):
        response = self._download(self.LIST_URL, 'csv')
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('.csv', response['Content-Disposition'])

    def test_excel_download(self):
        response = self._download(self.LIST_URL, 'excel')
        self.assertEqual(response['Content-Type'], XLSX)
        # A real xlsx is a zip archive — check the magic bytes, not just the header.
        self.assertTrue(response.content.startswith(b'PK'))

    def test_pdf_download(self):
        response = self._download(self.LIST_URL, 'pdf')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_an_unknown_format_is_refused(self):
        response = self.client.get(self.LIST_URL, {'file_format': 'docx'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_file_holds_only_this_student(self):
        """The queryset is the boundary — a classmate's score must not be in the file."""
        body = self._text(self._download(self.LIST_URL, 'csv'))
        self.assertIn(self.test.title, body)
        self.assertNotIn(self.classmate.full_name, body)

    def test_the_student_columns_carry_no_rank(self):
        body = self._text(self._download(self.LIST_URL, 'csv'))
        self.assertNotIn('Rank', body)

    def test_the_student_columns_are_the_compact_set(self):
        """No point repeating the downloader's own name on every row."""
        first_line = self._text(self._download(self.LIST_URL, 'csv')).splitlines()[0]
        self.assertIn('Subject', first_line)
        self.assertIn('Percentage', first_line)
        self.assertNotIn('Student', first_line)
        self.assertNotIn('Published', first_line)

    def test_unpublished_results_are_absent(self):
        """A student may only download what they are allowed to see."""
        self.result.is_published = False
        self.result.save(update_fields=['is_published'])

        body = self._text(self._download(self.LIST_URL, 'csv'))
        self.assertNotIn(self.test.title, body)

    def test_an_empty_history_still_downloads(self):
        """Nothing published is a valid state, not an error."""
        Result.objects.all().update(is_published=False)

        response = self._download(self.LIST_URL, 'pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))


class StaffExportIsUnchangedTests(ResultExportTestCase):
    """The wider staff column set must survive the student-facing change."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.teacher)

    def test_staff_columns_still_include_rank_and_student(self):
        first_line = self._text(self._download(self.LIST_URL, 'csv')).splitlines()[0]
        for column in ('Student', 'Email', 'Class', 'Section', 'Rank', 'Published'):
            self.assertIn(column, first_line)

    def test_staff_see_every_student(self):
        body = self._text(self._download(self.LIST_URL, 'csv'))
        self.assertIn(self.student.full_name, body)
        self.assertIn(self.classmate.full_name, body)

    def test_staff_can_download_pdf_too(self):
        response = self._download(self.LIST_URL, 'pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))


class SingleResultExportTests(ResultExportTestCase):
    """"Download Result" on one result — summary plus per-question breakdown."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.student)
        self.url = f'/api/v1/results/{self.result.pk}/export/'

    def test_pdf_is_the_default_format(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_csv_carries_the_summary(self):
        body = self._text(self._download(self.url, 'csv'))
        self.assertIn('Percentage', body)
        self.assertIn(self.test.title, body)

    def test_excel_download(self):
        response = self._download(self.url, 'excel')
        self.assertEqual(response['Content-Type'], XLSX)
        self.assertTrue(response.content.startswith(b'PK'))

    def test_the_filename_comes_from_the_test(self):
        response = self._download(self.url, 'pdf')
        self.assertIn('.pdf', response['Content-Disposition'])
        self.assertNotIn('results_', response['Content-Disposition'])

    def test_a_student_cannot_export_someone_elses_result(self):
        response = self.client.get(f'/api/v1/results/{self.other_result.pk}/export/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_rank_in_a_student_download(self):
        body = self._text(self._download(self.url, 'csv'))
        self.assertNotIn('Rank', body)

    def test_the_breakdown_follows_the_review_setting(self):
        """A student barred from reviewing on screen is not handed the answers in a file."""
        self.test.allow_review_after_submit = False
        self.test.save(update_fields=['allow_review_after_submit'])

        body = self._text(self._download(self.url, 'csv'))
        self.assertIn('Percentage', body)
        self.assertNotIn('Correct Answer', body)
