"""School provisioning business logic.

Creating a school and creating its first School Admin is a single unit of work: a school
nobody can log into is not a usable school, and an admin User pointing at a half-created
school is worse. Both happen inside one transaction — if any step raises, the whole thing
rolls back and no School, no Class/Section seed, and no User survive.
"""
from __future__ import annotations

import logging
import re

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import School

User = get_user_model()
logger = logging.getLogger(__name__)

SCHOOL_ID_PREFIX_LENGTH = 3
SCHOOL_ID_SEQUENCE_WIDTH = 3


def school_letter_prefix(school: School) -> str:
    """The school's three-letter ID prefix, e.g. Karimnagar Public School -> KAR.

    Shared by both the Student ID and Teacher ID schemes so their prefixes always agree.
    Derived from the school name so that the prefix is stable and meaningful even though
    the School ID itself is now a numeric sequence (e.g. 001).

    Letters only, right-padded with X so the prefix is always exactly three characters.
    """
    letters = re.sub(r'[^A-Z]', '', (school.name or '').upper())
    return (letters[:SCHOOL_ID_PREFIX_LENGTH] or 'SCH').ljust(SCHOOL_ID_PREFIX_LENGTH, 'X')


def generate_school_id(name: str) -> str:
    """Next free School ID, e.g. '001', '002', '003'…

    Format: a zero-padded numeric sequence, globally incremented across all schools.
    The school name is no longer embedded in the ID.
    """
    # Find the highest numeric-only code already in use.
    pattern = re.compile(r'^\d+$')
    highest = 0
    for code in School.objects.values_list('code', flat=True):
        match = pattern.match(code)
        if match:
            highest = max(highest, int(code))

    # Walk forward until the ID is genuinely free.
    candidate_number = highest + 1
    while True:
        candidate = f'{candidate_number:0{SCHOOL_ID_SEQUENCE_WIDTH}d}'
        if not School.objects.filter(code=candidate).exists():
            return candidate
        candidate_number += 1


@transaction.atomic
def delete_school(school: School) -> None:
    """Delete a school and everything belonging to it.

    Every School FK is CASCADE, but two of Class's children are PROTECT
    (`StudentProfile.school_class`, `Test.school_class`). Django's collector walks
    School -> Class and hits those protected rows before it ever reaches the CASCADE that
    would have removed them, so a plain `school.delete()` raises ProtectedError — a school
    with any student or test could not be deleted at all.

    Clearing the protected referrers first lets the ordinary cascade take care of the rest
    (users, classes, sections, subjects, questions). One transaction: a partial delete would
    leave the school in a worse state than not deleting it.
    """
    from apps.students.models import StudentProfile
    from apps.tests.models import Test

    # Tests before students: TestQuestion/TestAssignment/ExamSession/Result hang off Test,
    # and sessions also reference the student rows we are about to remove.
    Test.objects.filter(school=school).delete()
    StudentProfile.objects.filter(school=school).delete()
    school.delete()


@transaction.atomic
def create_school_with_admin(
    *,
    school_data: dict,
    admin_full_name: str,
    admin_email: str,
    admin_password: str | None = None,
    created_by: User | None = None,
) -> tuple[School, User]:
    """Create a School, seed its academic layout, and provision its first School Admin.

    `admin_email` is the admin's LOGIN email and lives only on the User. It is deliberately
    independent of `school_data['official_email']`, which is the school's contact address
    and is never a credential.

    Two provisioning modes, chosen by whether ``admin_password`` is given:

    - ``None`` (default, used by the API): the admin is created WITHOUT a usable password,
      exactly like a provisioned teacher. The caller then sends a one-time setup link the
      admin uses to set their own password. No forced-change flag is needed — the admin
      never receives a password to replace.
    - a value: the CSC Admin chose a temporary password. It is hashed by create_user()
      (plaintext is never stored) and flagged must_change_password=True so it is replaced
      on first login. Kept for internal/programmatic callers; the API no longer uses it.
    """
    # Imported lazily: apps.academics.services imports schools.models, so a module-level
    # import here would be circular.
    from apps.academics.services import seed_class_sections_for_school

    # The School ID is server-generated and never supplied by the client. Generated inside
    # the transaction so a concurrent create can't hand out the same sequence number (the
    # unique constraint on `code` is the final backstop).
    school_data = dict(school_data)
    school_data['code'] = generate_school_id(school_data.get('name', ''))

    school = School.objects.create(created_by=created_by, **school_data)

    # Seeds the supported grades and sections so the new school's dropdowns work at once.
    seed_class_sections_for_school(school)

    has_temp_password = bool(admin_password)
    admin = User.objects.create_user(
        email=admin_email,
        password=admin_password if has_temp_password else None,
        full_name=admin_full_name,
        role=User.Role.SCHOOL_ADMIN,
        school=school,
        # With a temp password a usable credential exists but is temporary; without one the
        # account has no usable password until the admin uses their setup link.
        is_password_set=has_temp_password,
        must_change_password=has_temp_password,
    )

    logger.info(
        'Provisioned school %s (%s) with school_admin %s',
        school.code, school.id, admin.email,
    )
    return school, admin
