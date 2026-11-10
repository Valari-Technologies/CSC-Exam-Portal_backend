"""Provision one working login for each of the four roles.

Idempotent: re-running updates the existing accounts' passwords and profiles rather
than creating duplicates, so it is safe to run against a database that already has
these demo users.

The passwords below are deliberately committed in plaintext because these are shared
demo/QA accounts, not real ones. Do NOT use this command to create an account that
guards real student data - provision those through the admin UI so the password is
chosen by the person who owns it.

Usage:
    python manage.py seed_login_credentials
    python manage.py seed_login_credentials --school-name "Demo Public School"
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academics.models import Class, Section
from apps.academics.services import seed_class_sections_for_school
from apps.authentication.models import User
from apps.schools.models import School
from apps.schools.services import generate_school_id
from apps.students.models import StudentProfile
from apps.students.services import generate_student_id
from apps.teachers.models import TeacherProfile
from apps.teachers.services import generate_teacher_id

# Each password satisfies the full policy: at least PASSWORD_MIN_LENGTH characters, with
# an uppercase letter, a lowercase letter, a digit and a special character.
CSC_ADMIN = {
    'email': 'cscadmin@cscexam.in',
    'password': 'CscAdmin@2026',
    'full_name': 'CSC Administrator',
}
SCHOOL_ADMIN = {
    'email': 'schooladmin@cscexam.in',
    'password': 'SchoolAdmin@2026',
    'full_name': 'Demo School Admin',
}
TEACHER = {
    'email': 'teacher@cscexam.in',
    'password': 'Teacher@2026',
    'full_name': 'Demo Teacher',
}
STUDENT = {
    'password': 'Student@2026',
    'full_name': 'Demo Student',
}

DEFAULT_SCHOOL_NAME = 'Demo Public School'


class Command(BaseCommand):
    help = 'Create/refresh one login per role (CSC Admin, School Admin, Teacher, Student).'

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            '--school-name',
            default=DEFAULT_SCHOOL_NAME,
            help=f'School the non-CSC roles belong to (default: "{DEFAULT_SCHOOL_NAME}").',
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        school = self._ensure_school(options['school_name'])
        rows = [
            self._ensure_csc_admin(),
            self._ensure_school_admin(school),
            self._ensure_teacher(school),
            self._ensure_student(school),
        ]
        self._report(school, rows)

    # ---------- school + academic layout ----------

    def _ensure_school(self, name: str) -> School:
        school = School.objects.filter(name=name).first()
        if school is None:
            school = School.objects.create(
                name=name,
                code=generate_school_id(name),
                address='1 Demo Street',
                city='Chennai',
                state='Tamil Nadu',
                pincode='600001',
                principal_name='Demo Principal',
                official_email='office@demoschool.example',
                contact_phone='9000000000',
                status=School.Status.ACTIVE,
                school_board=School.Board.STATE_BOARD,
            )
            self.stdout.write(f'Created school "{school.name}" (School ID {school.code}).')
        elif school.status != School.Status.ACTIVE:
            # Login is refused for any non-Active school, for every role.
            school.status = School.Status.ACTIVE
            school.save(update_fields=['status'])
            self.stdout.write(f'Reactivated school "{school.name}".')

        counts = seed_class_sections_for_school(school)
        self.stdout.write(
            f'Academic layout: +{counts["classes_created"]} classes, '
            f'+{counts["sections_created"]} sections.'
        )
        return school

    # ---------- per-role accounts ----------

    def _ensure_user(self, *, email, password, full_name, role, school, **extra):
        user = User.objects.filter(email=email).first()
        action = 'updated'
        if user is None:
            user = User(email=email, full_name=full_name, role=role, school=school)
            action = 'created'
        user.full_name = full_name
        user.role = role
        user.school = school
        user.is_active = True
        user.is_verified = True
        user.is_password_set = True
        # False on purpose: a temporary password would otherwise force a change screen
        # before these accounts could be used for sign-in testing.
        user.must_change_password = False
        for field, value in extra.items():
            setattr(user, field, value)
        user.set_password(password)
        user.save()
        return user, action

    def _ensure_csc_admin(self) -> dict:
        user, action = self._ensure_user(
            **CSC_ADMIN,
            role=User.Role.CSC_ADMIN,
            school=None,      # CSC Admin is platform-wide, never tied to a school.
            is_staff=True,    # Django admin access.
            is_superuser=True,
        )
        return {
            'role': 'CSC Admin',
            'login': user.email,
            'password': CSC_ADMIN['password'],
            'action': action,
        }

    def _ensure_school_admin(self, school: School) -> dict:
        user, action = self._ensure_user(
            **SCHOOL_ADMIN, role=User.Role.SCHOOL_ADMIN, school=school,
        )
        return {
            'role': 'School Admin',
            'login': user.email,
            'password': SCHOOL_ADMIN['password'],
            'action': action,
        }

    def _ensure_teacher(self, school: School) -> dict:
        user, action = self._ensure_user(**TEACHER, role=User.Role.TEACHER, school=school)
        profile = TeacherProfile.objects.filter(user=user).first()
        if profile is None:
            TeacherProfile.objects.create(
                user=user,
                school=school,
                teacher_id=generate_teacher_id(school),
                employee_id='EMP001',
                contact_number='9000000001',
                gender=TeacherProfile.Gender.OTHER,
                qualification='M.Sc., B.Ed.',
                joining_date=date.today(),
                is_active=True,
            )
        else:
            profile.is_active = True
            profile.save(update_fields=['is_active'])
        return {
            'role': 'Teacher',
            'login': user.email,
            'password': TEACHER['password'],
            'action': action,
        }

    def _ensure_student(self, school: School) -> dict:
        school_class = Class.objects.filter(school=school, numeric_value=10).first()
        section = Section.objects.filter(
            school=school, school_class=school_class, name='A',
        ).first()

        # Students sign in with a Student ID, not an email, so look the account up by the
        # profile rather than by email (which stays NULL for them).
        profile = StudentProfile.objects.filter(
            school=school,
            school_class=school_class,
            section=section,
            roll_number='1',
        ).select_related('user').first()

        if profile is not None:
            user = profile.user
            action = 'updated'
        else:
            user = User(full_name=STUDENT['full_name'], role=User.Role.STUDENT, school=school)
            action = 'created'

        if not user.student_id:
            user.student_id = generate_student_id(school)
        user.full_name = STUDENT['full_name']
        user.role = User.Role.STUDENT
        user.school = school
        user.email = None  # No login email; '' would collide on the unique index.
        user.is_active = True
        user.is_verified = True
        user.is_password_set = True
        user.must_change_password = False
        user.set_password(STUDENT['password'])
        user.save()

        if profile is None:
            StudentProfile.objects.create(
                user=user,
                school=school,
                school_class=school_class,
                section=section,
                roll_number='1',
                admission_number='ADM001',
                date_of_birth=date(2010, 1, 1),
                gender=StudentProfile.Gender.OTHER,
                parent_name='Demo Parent',
                parent_phone='9000000002',
                is_active=True,
            )
        else:
            profile.is_active = True
            profile.save(update_fields=['is_active'])

        return {
            'role': 'Student',
            'login': user.student_id,
            'password': STUDENT['password'],
            'action': action,
        }

    # ---------- output ----------

    def _report(self, school: School, rows: list) -> None:
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Credentials for "{school.name}" (School ID {school.code}):'
        ))
        self.stdout.write('')
        self.stdout.write(f'  {"ROLE":<14} {"LOGIN":<26} {"PASSWORD":<18} STATUS')
        self.stdout.write(f'  {"-" * 14} {"-" * 26} {"-" * 18} {"-" * 7}')
        for row in rows:
            self.stdout.write(
                f'  {row["role"]:<14} {row["login"]:<26} '
                f'{row["password"]:<18} {row["action"]}'
            )
        self.stdout.write('')
        self.stdout.write('  CSC Admin / School Admin / Teacher sign in with EMAIL at /login.')
        self.stdout.write('  Student signs in with the STUDENT ID at /studentlogin.')
