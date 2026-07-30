"""Question bulk import resolves subjects by Subject ID (Subject.code), not by name.

Subject names repeat across classes — a school teaching "Mathematics" in classes 1-10 has
ten subjects by that name — so the old subject_name column was inherently ambiguous. The
Subject ID is unique per school, so each row maps to exactly one subject.

The factory school code is 'SCH0042'-shaped, so generated Subject IDs here read 'SC_MAT_10'.
"""
import io

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from apps.academics.models import Chapter
from apps.academics.services import generate_subject_id
from apps.questions.bulk_import import validate_required_columns
from apps.questions.models import Question

from .factories import make_class, make_school, make_subject, make_user

HEADER = (
    'question_text,option_a,option_b,option_c,option_d,correct_option,'
    'subject_id,chapter_name,difficulty,marks'
)
ROW = 'What is 2 + 2?,3,4,5,6,b,{subject_id},Basic Arithmetic,easy,1'


def csv_file(text: str, name: str = 'questions.csv') -> SimpleUploadedFile:
    return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')


class QuestionBulkImportSubjectIdTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.class_10 = make_class(self.school, numeric_value=10)
        self.subject = make_subject(self.school, self.class_10, name='Mathematics')
        self.subject.code = generate_subject_id(self.school, 'Mathematics', 10)
        self.subject.save(update_fields=['code'])
        self.chapter = Chapter.objects.create(subject=self.subject, name='Basic Arithmetic')

        self.admin = make_user('school_admin', self.school)
        self.client.force_authenticate(self.admin)

    def _import(self, body: str):
        return self.client.post(
            '/api/v1/questions/bulk-import/',
            {'file': csv_file(body)},
            format='multipart',
        )

    def _import_rows(self, *subject_ids):
        rows = '\n'.join(ROW.format(subject_id=s) for s in subject_ids)
        return self._import(f'{HEADER}\n{rows}')

    def test_subject_id_is_a_required_column(self):
        missing = validate_required_columns(
            {'question_text', 'option_a', 'option_b', 'option_c', 'option_d',
             'correct_option', 'chapter_name', 'difficulty', 'marks'},
        )
        self.assertIn('subject_id', missing)

    def test_subject_name_no_longer_satisfies_the_subject_column(self):
        """A file written against the old template is rejected up front, not row by row."""
        body = (
            'question_text,option_a,option_b,option_c,option_d,correct_option,'
            'subject_name,chapter_name,difficulty,marks\n'
            'What is 2 + 2?,3,4,5,6,b,Mathematics,Basic Arithmetic,easy,1'
        )
        resp = self._import(body)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['success'], 0)
        self.assertIn('subject_id', resp.data['errors'][0]['error'])

    def test_valid_subject_id_imports_the_question(self):
        resp = self._import_rows(self.subject.code)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['success'], 1)
        self.assertEqual(resp.data['fail'], 0)
        question = Question.objects.get(school=self.school)
        self.assertEqual(question.subject_id, self.subject.pk)
        self.assertEqual(question.chapter_id, self.chapter.pk)

    def test_subject_id_matches_case_insensitively(self):
        resp = self._import_rows(self.subject.code.lower())

        self.assertEqual(resp.data['success'], 1)
        self.assertEqual(Question.objects.get(school=self.school).subject_id, self.subject.pk)

    def test_surrounding_whitespace_is_ignored(self):
        resp = self._import_rows(f'  {self.subject.code}  ')

        self.assertEqual(resp.data['success'], 1)

    def test_unknown_subject_id_reports_a_clear_error(self):
        resp = self._import_rows('SC_XYZ_99')

        self.assertEqual(resp.data['success'], 0)
        self.assertEqual(resp.data['fail'], 1)
        error = resp.data['errors'][0]
        self.assertEqual(error['row'], 2)
        self.assertIn('SC_XYZ_99', error['error'])
        self.assertIn('not found in this school', error['error'])

    def test_numeric_subject_id_is_rejected_with_migration_guidance(self):
        """The old template's numeric key must not silently resolve to some other subject."""
        resp = self._import_rows(str(self.subject.pk))

        self.assertEqual(resp.data['success'], 0)
        self.assertIn('Subject ID', resp.data['errors'][0]['error'])
        self.assertIn('not a number', resp.data['errors'][0]['error'])

    def test_blank_subject_id_reports_a_clear_error(self):
        resp = self._import_rows('')

        self.assertEqual(resp.data['success'], 0)
        self.assertIn('required', resp.data['errors'][0]['error'])

    def test_subject_id_from_another_school_is_rejected(self):
        """Resolution is school-scoped — the same Subject ID exists in other schools."""
        other_school = make_school(code='SCX9001')
        other_class = make_class(other_school, numeric_value=10)
        other_subject = make_subject(other_school, other_class, name='Mathematics')
        other_subject.code = generate_subject_id(other_school, 'Mathematics', 10)
        other_subject.save(update_fields=['code'])
        # Both schools' prefixes are 'SC', so the codes genuinely collide across schools.
        self.assertEqual(other_subject.code, self.subject.code)

        # The importing admin belongs to self.school, so the row must resolve there.
        resp = self._import_rows(other_subject.code)

        self.assertEqual(resp.data['success'], 1)
        self.assertEqual(Question.objects.get(school=self.school).subject_id, self.subject.pk)
        self.assertEqual(Question.objects.filter(school=other_school).count(), 0)

    def test_same_name_different_classes_resolve_to_distinct_subjects(self):
        """The conflict this change fixes: 'Mathematics' in two classes, two Subject IDs."""
        class_5 = make_class(self.school, numeric_value=5)
        maths_5 = make_subject(self.school, class_5, name='Mathematics')
        maths_5.code = generate_subject_id(self.school, 'Mathematics', 5)
        maths_5.save(update_fields=['code'])
        Chapter.objects.create(subject=maths_5, name='Basic Arithmetic')

        self.assertNotEqual(maths_5.code, self.subject.code)

        resp = self._import_rows(self.subject.code, maths_5.code)

        self.assertEqual(resp.data['success'], 2)
        self.assertEqual(
            set(Question.objects.values_list('subject_id', flat=True)),
            {self.subject.pk, maths_5.pk},
        )

    def test_chapter_must_belong_to_the_resolved_subject(self):
        class_5 = make_class(self.school, numeric_value=5)
        maths_5 = make_subject(self.school, class_5, name='Mathematics')
        maths_5.code = generate_subject_id(self.school, 'Mathematics', 5)
        maths_5.save(update_fields=['code'])

        # 'Basic Arithmetic' exists only under the class-10 subject.
        resp = self._import_rows(maths_5.code)

        self.assertEqual(resp.data['success'], 0)
        self.assertIn('Basic Arithmetic', resp.data['errors'][0]['error'])

    def test_excel_upload_resolves_subject_id(self):
        """Excel cells come back typed — the Subject ID must survive the parser."""
        wb = openpyxl.Workbook()
        wb.active.append(HEADER.split(','))
        wb.active.append(
            ROW.format(subject_id=self.subject.code).split(','),
        )
        buffer = io.BytesIO()
        wb.save(buffer)

        resp = self.client.post(
            '/api/v1/questions/bulk-import/',
            {'file': SimpleUploadedFile('questions.xlsx', buffer.getvalue())},
            format='multipart',
        )

        self.assertEqual(resp.data['success'], 1)


class QuestionImportTemplateTests(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.class_10 = make_class(self.school, numeric_value=10)
        self.admin = make_user('school_admin', self.school)
        self.client.force_authenticate(self.admin)

    def _download(self) -> str:
        resp = self.client.get('/api/v1/questions/import-template/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.content.decode('utf-8')

    def test_template_header_uses_subject_id_not_subject_name(self):
        header = self._download().splitlines()[0]

        self.assertIn('subject_id', header)
        self.assertNotIn('subject_name', header)

    def test_template_examples_use_real_subject_ids_from_the_school(self):
        subject = make_subject(self.school, self.class_10, name='Mathematics')
        subject.code = generate_subject_id(self.school, 'Mathematics', 10)
        subject.save(update_fields=['code'])

        self.assertIn(subject.code, self._download())

    def test_template_falls_back_to_a_sample_id_when_the_school_has_no_subjects(self):
        self.assertIn('KA_MAT_10', self._download())

    def test_downloaded_template_is_importable_as_is(self):
        """The examples must round-trip: download, upload, both rows land."""
        subject = make_subject(self.school, self.class_10, name='Mathematics')
        subject.code = generate_subject_id(self.school, 'Mathematics', 10)
        subject.save(update_fields=['code'])
        Chapter.objects.create(subject=subject, name='Basic Arithmetic')
        Chapter.objects.create(subject=subject, name='Indian States')

        resp = self.client.post(
            '/api/v1/questions/bulk-import/',
            {'file': csv_file(self._download())},
            format='multipart',
        )

        self.assertEqual(resp.data['success'], 2, resp.data['errors'])
