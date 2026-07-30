"""The PDF download is a report card; CSV and Excel stay flat data.

That split is the requirement, and it is easy to break by "unifying" the three
formats later — so the shape of each is asserted rather than assumed.

Report card content is checked by extracting the PDF's text. It is a coarse
check: it proves the school identity, the student's identity and the marks
reached the page, not that the layout is attractive. Nothing automated can judge
the latter.
"""
import base64
import re
import zlib
from decimal import Decimal
from io import BytesIO

from rest_framework import status
from rest_framework.test import APITestCase

from apps.results.models import Result
from apps.results.pdf import build_report_card_pdf

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


def _decode_stream(raw: bytes) -> bytes:
    """Undo reportlab's stream encoding.

    Page content is written ASCII85-then-Flate encoded, so the a85 layer has to come
    off before zlib will touch it. The plainer forms are tried afterwards so this
    keeps working if reportlab's defaults ever change.
    """
    raw = raw.strip()
    attempts = (
        lambda b: zlib.decompress(base64.a85decode(b.rstrip(b'~>'), adobe=False)),
        zlib.decompress,
        lambda b: base64.a85decode(b.rstrip(b'~>'), adobe=False),
        lambda b: b,
    )
    for attempt in attempts:
        try:
            return attempt(raw)
        except Exception:
            continue
    return b''


def pdf_text(payload: bytes) -> str:
    """The literal strings drawn on the page, in order.

    Coarse by design: it proves a value reached the page, not that the page looks
    good. Text-showing operators carry their strings in parentheses, which is what
    is pulled out here.
    """
    blob = b''.join(
        _decode_stream(match.group(1))
        for match in re.finditer(rb'stream\r?\n(.*?)endstream', payload, re.DOTALL)
    )
    return ' '.join(m.group(1).decode('latin-1') for m in re.finditer(rb'\((.*?)\)', blob))


class ReportCardTestCase(APITestCase):
    """A school with an ID, one student, two subjects."""

    LIST_URL = '/api/v1/results/export/'

    def setUp(self):
        self.school = make_school()
        self.school.name = 'Karapettai Nadar Hr Sec School'
        self.school.code = 'KAR_001'
        self.school.principal_name = 'R Ganesan'
        self.school.save(update_fields=['name', 'code', 'principal_name'])

        self.klass = make_class(self.school, numeric_value=10)
        self.section = make_section(self.school, self.klass, name='A')
        self.teacher = make_user('teacher', self.school, full_name='Meena Iyer')

        self.student = make_student(
            self.school, self.klass, self.section, student_id='KAR_001-0002',
        )
        self.student.full_name = 'Muthu Subash K'
        self.student.save(update_fields=['full_name'])

        self.maths = self._subject_result('Mathematics', Decimal('18'), Decimal('20'))
        self.science = self._subject_result('Science', Decimal('12'), Decimal('20'))

        self.client.force_authenticate(self.student)

    def _subject_result(self, subject_name: str, obtained, total) -> Result:
        subject = make_subject(self.school, self.klass)
        subject.name = subject_name
        subject.save(update_fields=['name'])
        chapter = make_chapter(subject)
        question = make_question(
            self.school, subject, chapter, correct_option='a', marks=Decimal('10'),
        )
        test = make_test(
            self.school, subject, self.klass, self.teacher, questions=[question],
        )
        test.title = f'{subject_name} Term Exam'
        test.save(update_fields=['title'])
        assignment = make_assignment(test, self.klass, self.teacher)
        session = make_session(self.student, assignment, test)
        return make_result(session, obtained=obtained, total=total, is_published=True)

    def _pdf(self, url=None) -> str:
        response = self.client.get(url or self.LIST_URL, {'file_format': 'pdf'})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content[:300])
        self.assertTrue(response.content.startswith(b'%PDF'))
        return pdf_text(response.content)


class StudentReportCardTests(ReportCardTestCase):
    """My Results -> PDF."""

    def test_the_school_identity_is_on_the_card(self):
        text = self._pdf()
        self.assertIn('Karapettai', text)
        self.assertIn('KAR_001', text)

    def test_the_school_id_is_the_code_not_the_primary_key(self):
        """The whole point of the School ID work — never print the internal id."""
        text = self._pdf()
        self.assertIn('KAR_001', text)
        self.assertNotIn(f'School ID: {self.school.pk}', text)

    def test_the_student_identity_is_on_the_card(self):
        text = self._pdf()
        self.assertIn('Muthu', text)
        self.assertIn('KAR_001-0002', text)

    def test_it_is_titled_as_a_report_card(self):
        self.assertIn('REPORT CARD', self._pdf())

    def test_every_subject_appears(self):
        text = self._pdf()
        self.assertIn('Mathematics', text)
        self.assertIn('Science', text)

    def test_the_totals_row_is_present(self):
        text = self._pdf()
        self.assertIn('TOTAL', text)
        # 18 + 12 out of 20 + 20.
        self.assertIn('30', text)
        self.assertIn('40', text)

    def test_the_signature_lines_are_present(self):
        text = self._pdf()
        self.assertIn('Signature', text)
        self.assertIn('Ganesan', text)

    def test_the_generation_date_is_stated(self):
        self.assertIn('Report generated on', self._pdf())

    def test_the_filename_says_report_card(self):
        response = self.client.get(self.LIST_URL, {'file_format': 'pdf'})
        self.assertIn('report_card', response['Content-Disposition'])


class OtherFormatsAreUnchangedTests(ReportCardTestCase):
    """The report card layout is for PDF ONLY."""

    def test_csv_is_still_a_flat_grid(self):
        response = self.client.get(self.LIST_URL, {'file_format': 'csv'})
        body = response.content.decode('utf-8')
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertNotIn('REPORT CARD', body)
        self.assertTrue(body.splitlines()[0].startswith('Subject,'))

    def test_excel_is_still_a_workbook(self):
        response = self.client.get(self.LIST_URL, {'file_format': 'excel'})
        self.assertTrue(response.content.startswith(b'PK'))


class TeacherReportCardTests(ReportCardTestCase):
    """Publish Results -> Export PDF, one card per student, same layout."""

    def setUp(self):
        super().setUp()
        self.classmate = make_student(
            self.school, self.klass, self.section, student_id='KAR_001-0003',
        )
        self.classmate.full_name = 'Arun Kumar'
        self.classmate.save(update_fields=['full_name'])

        session = make_session(self.classmate, self.maths.assignment, self.maths.test)
        make_result(
            session, obtained=Decimal('7'), total=Decimal('20'), is_published=True,
        )
        self.client.force_authenticate(self.teacher)

    def test_the_teacher_pdf_is_the_same_report_card(self):
        text = self._pdf()
        self.assertIn('REPORT CARD', text)
        self.assertIn('KAR_001', text)

    def test_every_student_gets_a_card(self):
        text = self._pdf()
        self.assertIn('Muthu', text)
        self.assertIn('Arun', text)

    def test_one_card_per_student_not_one_per_result(self):
        """Three results, two students — so two cards.

        Counted by how often a student's NAME is printed rather than by counting
        headings: the name appears once per card, so a card-per-result grouping
        would print this student twice. (Heading text is not a reliable counter —
        the renderer is free to split a string across text operators.)
        """
        text = pdf_text(self.client.get(self.LIST_URL, {'file_format': 'pdf'}).content)
        self.assertEqual(text.count('Subash'), 1)
        self.assertEqual(text.count('Arun'), 1)

    def test_a_students_own_download_holds_only_their_card(self):
        self.client.force_authenticate(self.student)
        text = self._pdf()
        self.assertIn('Muthu', text)
        self.assertNotIn('Arun', text)


class ReportCardBuilderTests(APITestCase):
    """Edge cases in the renderer that are awkward to force through the API."""

    SCHOOL = {
        'name': 'Test School', 'code': 'TST_001',
        'principal_name': None, 'address_line': None, 'logo_path': None,
    }

    def _card(self, **overrides) -> dict:
        card = {
            'student': {
                'name': 'A Student', 'student_id': 'TST_001-0001',
                'class_name': '10', 'section_name': 'A',
            },
            'rows': [{
                'subject': 'Maths', 'exam': 'Term 1', 'obtained': '9',
                'total': '10', 'percentage': '90%', 'result': 'Pass',
            }],
            'totals': {
                'obtained': '9', 'total': '10', 'percentage': '90.0%', 'result': 'Pass',
            },
            'teacher_name': None,
        }
        card.update(overrides)
        return card

    def test_a_missing_logo_still_renders(self):
        """A school with no crest must still get a card, not a 500."""
        payload = build_report_card_pdf(
            school=self.SCHOOL, cards=[self._card()], generated_on='01 Jan 2026',
        )
        self.assertTrue(payload.startswith(b'%PDF'))

    def test_a_broken_logo_path_still_renders(self):
        school = dict(self.SCHOOL, logo_path='/nope/not-here.png')
        payload = build_report_card_pdf(
            school=school, cards=[self._card()], generated_on='01 Jan 2026',
        )
        self.assertTrue(payload.startswith(b'%PDF'))

    def test_no_cards_produces_a_valid_document(self):
        """Nothing published is a legitimate outcome, not an error."""
        payload = build_report_card_pdf(
            school=self.SCHOOL, cards=[], generated_on='01 Jan 2026',
        )
        self.assertTrue(payload.startswith(b'%PDF'))
        self.assertIn('No published results', pdf_text(payload))

    def test_a_student_with_no_profile_still_renders(self):
        """Class and section are blank rather than fatal."""
        card = self._card(student={
            'name': 'A Student', 'student_id': None,
            'class_name': None, 'section_name': None,
        })
        payload = build_report_card_pdf(
            school=self.SCHOOL, cards=[card], generated_on='01 Jan 2026',
        )
        self.assertTrue(payload.startswith(b'%PDF'))

    def test_the_output_is_a_real_pdf_not_just_a_prefix(self):
        payload = build_report_card_pdf(
            school=self.SCHOOL, cards=[self._card()], generated_on='01 Jan 2026',
        )
        self.assertTrue(payload.rstrip().endswith(b'%%EOF'))
        self.assertGreater(len(payload), 1000)
        BytesIO(payload)  # cheap sanity that it is bytes, not a lazy object
