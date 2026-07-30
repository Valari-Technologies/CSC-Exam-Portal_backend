"""Student bulk import: file parsing, validation, templates, and history deletion.

The bugs these pin were all reproduced against the real app first. The headline one: a
file whose headings read "Email, Full Name, Roll Number" — i.e. what a human types into
Excel — was rejected with "Missing columns: email, full_name, roll_number", naming the
very columns the file had.
"""
import io
from datetime import date, datetime

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from apps.students.file_parsers import normalize_header, parse_upload_file
from apps.schools.services import school_letter_prefix
from apps.students.import_template import ALL_COLUMNS, REQUIRED_COLUMNS
from apps.students.models import BulkImportLog, StudentProfile

from .factories import make_class, make_school, make_section, make_user

User = get_user_model()


def csv_file(text: str, name: str = 'students.csv') -> SimpleUploadedFile:
    return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')


def xlsx_file(rows: list, name: str = 'students.xlsx') -> SimpleUploadedFile:
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return SimpleUploadedFile(name, buffer.getvalue())


class HeaderNormalizationTests(APITestCase):
    def test_folds_spacing_case_and_punctuation(self):
        for raw in ('full_name', 'Full Name', 'FULL NAME', ' Full-Name ', 'full  name'):
            self.assertEqual(normalize_header(raw), 'full_name', msg=raw)

    def test_blank_and_none_headers_are_empty(self):
        self.assertEqual(normalize_header(None), '')
        self.assertEqual(normalize_header('   '), '')

    def test_parser_returns_normalized_keys(self):
        rows = parse_upload_file(csv_file('Email,Full Name,Roll Number\na@b.com,A B,7\n'))
        self.assertEqual(rows, [{'email': 'a@b.com', 'full_name': 'A B', 'roll_number': '7'}])

    def test_parser_drops_blank_rows(self):
        # Excel leaves a trailing newline when saving as CSV; that is not a student.
        rows = parse_upload_file(csv_file('email,full_name,roll_number\na@b.com,A B,7\n,,\n'))
        self.assertEqual(len(rows), 1)

    def test_excel_integer_cells_do_not_become_floats(self):
        rows = parse_upload_file(xlsx_file([
            ['email', 'full_name', 'roll_number'],
            ['a@b.com', 'A B', 12],
        ]))
        self.assertEqual(rows[0]['roll_number'], '12')


class BulkImportBase(APITestCase):
    def setUp(self):
        self.school = make_school()
        self.admin = make_user('school_admin', self.school)
        self.klass = make_class(self.school)
        self.section = make_section(self.school, self.klass)
        self.client.force_authenticate(self.admin)

    def upload(self, file_obj, **overrides):
        payload = {
            'file': file_obj,
            'school_class': self.klass.pk,
            'section': self.section.pk,
        }
        payload.update(overrides)
        return self.client.post(
            '/api/v1/students/bulk-import/', payload, format='multipart',
        )


class BulkImportTests(BulkImportBase):
    def test_human_typed_headers_are_accepted(self):
        """The exact file shape that failed in production."""
        response = self.upload(csv_file(
            'Email,Full Name,Roll Number\nana@example.com,Ana Roy,1\n'
        ))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'completed')
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(response.data['fail_count'], 0)
        self.assertTrue(User.objects.filter(email='ana@example.com').exists())

    def test_trailing_blank_line_is_not_a_failed_row(self):
        response = self.upload(csv_file(
            'email,full_name,roll_number\nana@example.com,Ana Roy,1\n\n'
        ))
        self.assertEqual(response.data['total_rows'], 1)
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(response.data['fail_count'], 0)

    def test_missing_column_message_names_what_is_missing_and_what_was_found(self):
        response = self.upload(csv_file('email,full_name\nana@example.com,Ana Roy\n'))
        self.assertEqual(response.data['status'], 'failed')
        message = response.data['errors'][0]['error']
        self.assertIn('roll_number', message)
        self.assertIn('email, full_name', message)  # what the file does have

    def test_empty_file_is_rejected_with_a_useful_message(self):
        response = self.upload(csv_file('email,full_name,roll_number\n'))
        self.assertEqual(response.data['status'], 'failed')
        self.assertIn('no data rows', response.data['errors'][0]['error'])

    def test_gender_aliases_are_normalized(self):
        response = self.upload(csv_file(
            'email,full_name,roll_number,gender\n'
            'a@example.com,A One,1,M\n'
            'b@example.com,B Two,2,Female\n'
            'c@example.com,C Three,3,\n'
        ))
        self.assertEqual(response.data['success_count'], 3)
        genders = {
            p.user.email: p.gender
            for p in StudentProfile.objects.select_related('user')
        }
        self.assertEqual(genders['a@example.com'], 'male')
        self.assertEqual(genders['b@example.com'], 'female')
        self.assertEqual(genders['c@example.com'], '')

    def test_unrecognised_gender_is_rejected_not_stored(self):
        response = self.upload(csv_file(
            'email,full_name,roll_number,gender\na@example.com,A One,1,Yes\n'
        ))
        self.assertEqual(response.data['success_count'], 0)
        self.assertEqual(response.data['fail_count'], 1)
        self.assertIn('not a valid gender', response.data['errors'][0]['error'])
        self.assertFalse(StudentProfile.objects.exists())

    def test_date_of_birth_is_imported_and_stored(self):
        response = self.upload(csv_file(
            'full_name,roll_number,date_of_birth\nA One,1,2015-03-21\n'
        ))
        self.assertEqual(response.data['success_count'], 1)
        profile = StudentProfile.objects.get()
        self.assertEqual(profile.date_of_birth, date(2015, 3, 21))

    def test_blank_date_of_birth_is_allowed_and_stored_as_null(self):
        response = self.upload(csv_file(
            'full_name,roll_number,date_of_birth\nA One,1,\n'
        ))
        self.assertEqual(response.data['success_count'], 1)
        self.assertIsNone(StudentProfile.objects.get().date_of_birth)

    def test_unparseable_date_of_birth_fails_the_row(self):
        response = self.upload(csv_file(
            'full_name,roll_number,date_of_birth\nA One,1,not-a-date\n'
        ))
        self.assertEqual(response.data['success_count'], 0)
        self.assertEqual(response.data['fail_count'], 1)
        self.assertIn('date of birth', response.data['errors'][0]['error'])
        self.assertFalse(StudentProfile.objects.exists())

    def test_excel_date_cell_is_imported(self):
        """openpyxl hands a date cell back as a datetime; the importer must still read it."""
        response = self.upload(xlsx_file([
            ['full_name', 'roll_number', 'date_of_birth'],
            ['A One', 1, datetime(2015, 3, 21)],
        ]))
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(StudentProfile.objects.get().date_of_birth, date(2015, 3, 21))

    def test_duplicate_email_within_the_file_is_reported_against_the_file(self):
        response = self.upload(csv_file(
            'email,full_name,roll_number\n'
            'a@example.com,A One,1\n'
            'a@example.com,A Again,2\n'
        ))
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(response.data['fail_count'], 1)
        self.assertIn('Duplicate email in this file', response.data['errors'][0]['error'])

    def test_duplicate_roll_number_within_the_file_is_reported(self):
        response = self.upload(csv_file(
            'email,full_name,roll_number\n'
            'a@example.com,A One,1\n'
            'b@example.com,B Two,1\n'
        ))
        self.assertEqual(response.data['fail_count'], 1)
        self.assertIn('Duplicate roll number', response.data['errors'][0]['error'])

    def test_partial_import_is_completed_and_counts_only_hard_failures(self):
        response = self.upload(csv_file(
            'email,full_name,roll_number\n'
            'good@example.com,Good Row,1\n'
            'noname@example.com,,2\n'
        ))
        self.assertEqual(response.data['status'], 'completed')
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(response.data['fail_count'], 1)
        self.assertIn('Missing required value(s): full_name', response.data['errors'][0]['error'])

    def test_import_with_no_successful_row_is_failed(self):
        # Blank name, and a blank line — not a blank email, which is now legitimate.
        response = self.upload(csv_file('email,full_name,roll_number\n,,\n,,2\n'))
        self.assertEqual(response.data['status'], 'failed')
        self.assertEqual(response.data['success_count'], 0)

    def test_unknown_columns_warn_but_do_not_fail_the_import(self):
        response = self.upload(csv_file(
            'email,full_name,roll_number,favourite_colour\na@example.com,A One,1,blue\n'
        ))
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(response.data['fail_count'], 0)
        warning = response.data['errors'][0]
        self.assertEqual(warning['level'], 'warning')
        self.assertIn('favourite_colour', warning['error'])

    def test_xlsx_import_works(self):
        response = self.upload(xlsx_file([
            ['Email', 'Full Name', 'Roll Number'],
            ['ana@example.com', 'Ana Roy', 1],
        ]))
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(
            StudentProfile.objects.get(user__email='ana@example.com').roll_number, '1',
        )

    def test_legacy_xls_is_rejected_with_guidance_not_a_500(self):
        """A real .xls is an OLE2 file; openpyxl reads .xlsx only, so this used to raise
        BadZipFile straight out of the view."""
        ole2_header = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 64
        response = self.upload(SimpleUploadedFile('legacy.xls', ole2_header))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'failed')
        self.assertIn('.xls', response.data['errors'][0]['error'])
        self.assertIn('xlsx', response.data['errors'][0]['error'])

    def test_a_corrupt_file_is_rejected_cleanly(self):
        """Anything the reader chokes on must be a rejection, never a 500."""
        response = self.upload(SimpleUploadedFile('broken.xlsx', b'not really a spreadsheet'))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'failed')
        self.assertIn('could not be read', response.data['errors'][0]['error'])

    def test_unsupported_format_is_rejected_before_any_work(self):
        response = self.upload(SimpleUploadedFile('students.pdf', b'%PDF-1.4'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(BulkImportLog.objects.exists())

    def test_section_must_belong_to_the_class(self):
        other_class = make_class(self.school, numeric_value=9)
        other_section = make_section(self.school, other_class)
        response = self.upload(
            csv_file('email,full_name,roll_number\na@example.com,A,1\n'),
            section=other_section.pk,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class OptionalEmailTests(BulkImportBase):
    """Email is optional for students — they sign in with a Student ID.

    The trap throughout: a blank email must reach the database as NULL. Postgres allows
    any number of NULLs under a unique index but only ever one '', so storing '' would
    let exactly one email-less student exist and fail the next one.
    """

    def test_a_file_with_no_email_column_at_all_imports(self):
        response = self.upload(csv_file('full_name,roll_number\nAna Roy,1\n'))
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(response.data['fail_count'], 0)

    def test_several_students_without_email_do_not_collide(self):
        """Two blank emails stored as '' would violate the unique index on the second."""
        response = self.upload(csv_file(
            'full_name,email,roll_number\nA One,,1\nB Two,,2\nC Three,,3\n'
        ))
        self.assertEqual(response.data['success_count'], 3)
        self.assertEqual(response.data['fail_count'], 0)

    def test_a_blank_email_is_stored_as_null_not_empty_string(self):
        self.upload(csv_file('full_name,roll_number\nAna Roy,1\n'))
        student = User.objects.get(role='student')
        self.assertIsNone(student.email)

    def test_a_supplied_email_is_still_saved_and_still_unique(self):
        self.upload(csv_file('full_name,email,roll_number\nA One,taken@example.com,1\n'))
        self.assertEqual(User.objects.filter(email='taken@example.com').count(), 1)

        response = self.upload(csv_file(
            'full_name,email,roll_number\nB Two,taken@example.com,2\n'
        ))
        self.assertEqual(response.data['fail_count'], 1)
        self.assertIn('already exists', response.data['errors'][0]['error'])

    def test_duplicate_emails_within_the_file_are_still_caught(self):
        response = self.upload(csv_file(
            'full_name,email,roll_number\nA One,same@example.com,1\nB Two,same@example.com,2\n'
        ))
        self.assertEqual(response.data['fail_count'], 1)
        self.assertIn('Duplicate email', response.data['errors'][0]['error'])

    def test_email_is_not_a_required_column(self):
        self.assertNotIn('email', REQUIRED_COLUMNS)
        self.assertIn('email', ALL_COLUMNS)


class ImportedCredentialTests(BulkImportBase):
    """Every imported student must get working login credentials.

    They used to be created with an unusable password plus an emailed setup link, which
    meant they could not sign in at all — and now that email is optional, there is often
    no inbox for that link to reach.
    """

    def upload_three(self):
        return self.upload(csv_file(
            'full_name,email,roll_number\n'
            'A One,a@example.com,1\n'
            'B Two,,2\n'
            'C Three,,3\n'
        ))

    def test_every_imported_student_gets_a_unique_student_id_and_password(self):
        response = self.upload_three()

        credentials = response.data['credentials']
        self.assertEqual(response.data['success_count'], 3)
        self.assertEqual(len(credentials), 3)

        student_ids = [c['student_id'] for c in credentials]
        self.assertEqual(len(set(student_ids)), 3, 'Student IDs collided')
        self.assertTrue(all(c['password'] for c in credentials))
        # Sequential in the KAR_ST_001 format, from the school's letter prefix, no gaps.
        prefix = school_letter_prefix(self.school)
        self.assertEqual(sorted(student_ids), [f'{prefix}_ST_00{n}' for n in (1, 2, 3)])

    def test_generated_credentials_actually_authenticate(self):
        """The end-to-end claim: take what the import handed back, and sign in with it."""
        credentials = self.upload_three().data['credentials']

        for credential in credentials:
            response = self.client.post('/api/v1/auth/student/login/', {
                'student_id': credential['student_id'],
                'password': credential['password'],
            })
            self.assertEqual(
                response.status_code, status.HTTP_200_OK,
                f"{credential['student_id']} could not log in: {response.data}",
            )
            self.assertIn('access', response.data)

    def test_imported_students_have_a_usable_password(self):
        self.upload_three()
        students = User.objects.filter(role='student')
        self.assertEqual(students.count(), 3)
        for student in students:
            self.assertTrue(student.is_password_set)
            self.assertTrue(student.has_usable_password(), f'{student.student_id} cannot log in')

    def test_student_ids_continue_from_the_ones_already_issued(self):
        """A second import must not restart the sequence and collide."""
        first = self.upload(csv_file('full_name,roll_number\nA One,1\n'))
        second = self.upload(csv_file('full_name,roll_number\nB Two,2\n'))

        prefix = school_letter_prefix(self.school)
        self.assertEqual(first.data['credentials'][0]['student_id'], f'{prefix}_ST_001')
        self.assertEqual(second.data['credentials'][0]['student_id'], f'{prefix}_ST_002')

    def test_passwords_are_never_persisted(self):
        """The plaintext exists only in the upload response — never in the log it wrote."""
        response = self.upload_three()
        password = response.data['credentials'][0]['password']

        log = BulkImportLog.objects.get(pk=response.data['id'])
        self.assertNotIn(password, str(log.errors))
        # And a later read of the same log offers no credentials at all.
        history = self.client.get('/api/v1/students/import-logs/')
        self.assertIsNone(history.data['results'][0]['credentials'])


class ImportTemplateTests(BulkImportBase):
    def test_csv_template_downloads(self):
        response = self.client.get('/api/v1/students/import-template/?file_format=csv')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('.csv', response['Content-Disposition'])

    def test_xlsx_template_downloads_as_a_spreadsheet(self):
        response = self.client.get('/api/v1/students/import-template/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def test_bad_format_is_rejected(self):
        response = self.client.get('/api/v1/students/import-template/?file_format=pdf')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_template_contains_every_column(self):
        response = self.client.get('/api/v1/students/import-template/?file_format=csv')
        header = b''.join(response.streaming_content).decode('utf-8-sig').splitlines()[0]
        self.assertEqual(header.split(','), list(ALL_COLUMNS))
        for column in REQUIRED_COLUMNS:
            self.assertIn(column, header)

    def test_the_template_presents_email_as_optional(self):
        """#4: the template has to say email is optional, and show what that looks like."""
        from apps.students.import_template import COLUMN_HELP, SAMPLE_ROWS

        self.assertIn('Optional', COLUMN_HELP['email'])
        # A sample row that leaves email blank — the format is easier to copy than to read.
        self.assertTrue(
            any(not row['email'] for row in SAMPLE_ROWS),
            'No sample row demonstrates an email-less student',
        )

    def test_the_downloaded_csv_template_imports_cleanly(self):
        """The contract: whatever we hand out must be a file we accept back.

        One of the two sample rows has no email, so this also proves an email-less row
        survives the whole round trip.
        """
        response = self.client.get('/api/v1/students/import-template/?file_format=csv')
        content = b''.join(response.streaming_content)

        result = self.upload(SimpleUploadedFile('template.csv', content))
        self.assertEqual(result.data['status'], 'completed')
        self.assertEqual(result.data['fail_count'], 0)
        self.assertEqual(result.data['success_count'], 2)  # the two sample rows

    def test_the_downloaded_xlsx_template_imports_cleanly(self):
        response = self.client.get('/api/v1/students/import-template/?file_format=xlsx')
        content = b''.join(response.streaming_content)

        result = self.upload(SimpleUploadedFile('template.xlsx', content))
        self.assertEqual(result.data['status'], 'completed')
        self.assertEqual(result.data['fail_count'], 0)
        self.assertEqual(result.data['success_count'], 2)

    def test_students_cannot_download_the_template(self):
        student = make_user('student', self.school)
        self.client.force_authenticate(student)
        response = self.client.get('/api/v1/students/import-template/?file_format=csv')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ImportHistoryDeletionTests(BulkImportBase):
    def _make_log(self, school=None) -> BulkImportLog:
        return BulkImportLog.objects.create(
            school=school or self.school,
            imported_by=self.admin,
            file_name='old.csv',
            total_rows=1,
            success_count=1,
            status=BulkImportLog.Status.COMPLETED,
        )

    def test_admin_can_delete_a_history_record(self):
        log = self._make_log()
        response = self.client.delete(f'/api/v1/students/import-logs/{log.pk}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BulkImportLog.objects.filter(pk=log.pk).exists())

    def test_deleting_a_record_keeps_the_students_it_imported(self):
        """The history is a receipt, not the students. Deleting it must not touch them."""
        self.upload(csv_file('email,full_name,roll_number\nana@example.com,Ana Roy,1\n'))
        log = BulkImportLog.objects.get()
        self.assertEqual(StudentProfile.objects.count(), 1)

        self.client.delete(f'/api/v1/students/import-logs/{log.pk}/')

        self.assertFalse(BulkImportLog.objects.exists())
        self.assertEqual(StudentProfile.objects.count(), 1)
        self.assertTrue(User.objects.filter(email='ana@example.com', is_active=True).exists())

    def test_deletion_is_audit_logged(self):
        from apps.audit.models import AuditLog

        log = self._make_log()
        self.client.delete(f'/api/v1/students/import-logs/{log.pk}/')
        entry = AuditLog.objects.filter(action=AuditLog.Action.BULK_IMPORT_DELETED).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.entity_id, log.pk)

    def test_cannot_delete_another_schools_history(self):
        other = self._make_log(school=make_school())
        response = self.client.delete(f'/api/v1/students/import-logs/{other.pk}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(BulkImportLog.objects.filter(pk=other.pk).exists())

    def test_missing_record_is_404(self):
        response = self.client.delete('/api/v1/students/import-logs/999999/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_cannot_delete_history(self):
        log = self._make_log()
        self.client.force_authenticate(make_user('teacher', self.school))
        response = self.client.delete(f'/api/v1/students/import-logs/{log.pk}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(BulkImportLog.objects.filter(pk=log.pk).exists())
