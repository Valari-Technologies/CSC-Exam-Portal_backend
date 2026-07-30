# Phase 2: Academic structure helpers
"""Reusable academic-structure helpers.

`seed_class_sections_for_school` is the single source of truth for the standard
academic layout (Classes 1-10, Sections A-F). It is called both by the
`seed_class_sections` management command and by the school-create API so every
new school is provisioned automatically.

Class names are the plain numeric string ("1".."10"); the bank/dropdowns display
just the number. Re-running normalizes any legacy ordinal names ("1st" -> "1").

The range must stay within MIN_GRADE..MAX_GRADE: the `classes` table has a DB
check constraint on `numeric_value`, so seeding outside it raises IntegrityError.
"""
from __future__ import annotations

import re

from apps.schools.services import school_letter_prefix

from .models import MAX_GRADE, MIN_GRADE, Class, Section, Subject

# Sections A-F. Narrowed from A-H on 2026-07-16; the unused G/H sections were removed by
# academics migration 0004. Seeding is idempotent, so it never recreates them.
SECTION_NAMES = ['A', 'B', 'C', 'D', 'E', 'F']

# Three-letter abbreviations for the standard subjects. Anything not listed falls back to
# the first three letters of the name (see subject_letter_prefix). Keys are matched
# case-insensitively on the trimmed name.
SUBJECT_PREFIX_MAP = {
    'mathematics': 'MAT',
    'maths': 'MAT',
    'english': 'ENG',
    'tamil': 'TAM',
    'science': 'SCI',
    'social science': 'SOC',
    'social': 'SOC',
}

SUBJECT_PREFIX_LENGTH = 3
# The Subject ID uses the first two letters of the school's (three-letter) ID prefix.
SUBJECT_SCHOOL_PREFIX_LENGTH = 2


def subject_letter_prefix(name: str) -> str:
    """Three-letter abbreviation for a subject name, e.g. 'Mathematics' -> 'MAT'.

    Standard subjects use the fixed map; any other name falls back to its first three
    letters (uppercased, right-padded with X so the result is always three characters).
    """
    key = (name or '').strip().lower()
    if key in SUBJECT_PREFIX_MAP:
        return SUBJECT_PREFIX_MAP[key]
    letters = re.sub(r'[^A-Z]', '', (name or '').upper())
    return (letters[:SUBJECT_PREFIX_LENGTH] or 'SUB').ljust(SUBJECT_PREFIX_LENGTH, 'X')


def generate_subject_id(
    school, name: str, numeric_value: int, exclude_pk: int | None = None,
) -> str:
    """Build a Subject ID like 'KA_MAT_10' — unique within the school.

    Format: the first two letters of the school's ID prefix, the subject's three-letter
    abbreviation, and the zero-padded (two-digit) class/grade number. The base value is
    deterministic; on the rare chance two different subject names in the same class collapse
    to the same abbreviation, a numeric suffix (_2, _3, ...) keeps it unique per school.
    `exclude_pk` lets an update ignore the subject's own current code when regenerating.
    """
    school_prefix = school_letter_prefix(school)[:SUBJECT_SCHOOL_PREFIX_LENGTH]
    base = f'{school_prefix}_{subject_letter_prefix(name)}_{numeric_value:02d}'

    def _taken(candidate: str) -> bool:
        qs = Subject.objects.filter(school=school, code=candidate)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        return qs.exists()

    if not _taken(base):
        return base
    suffix = 2
    while _taken(f'{base}_{suffix}'):
        suffix += 1
    return f'{base}_{suffix}'


def seed_class_sections_for_school(school) -> dict:
    """Ensure `school` has Classes 1-10 and Sections A-F (idempotent).

    Looks each class up by `numeric_value` so legacy ordinal names ("1st") are
    renamed to the numeric form ("1"). Returns a dict of counts for reporting.
    """
    classes_created = sections_created = normalized = 0

    for n in range(MIN_GRADE, MAX_GRADE + 1):
        name = str(n)
        school_class = Class.objects.filter(school=school, numeric_value=n).first()
        if school_class is None:
            school_class, created = Class.objects.get_or_create(
                school=school,
                name=name,
                defaults={'numeric_value': n, 'is_active': True},
            )
            if created:
                classes_created += 1
        elif school_class.name != name:
            # Normalize a legacy ordinal name ("1st" -> "1").
            school_class.name = name
            school_class.save(update_fields=['name'])
            normalized += 1

        for section_name in SECTION_NAMES:
            _, sec_created = Section.objects.get_or_create(
                school=school,
                school_class=school_class,
                name=section_name,
                defaults={'is_active': True},
            )
            if sec_created:
                sections_created += 1

    return {
        'classes_created': classes_created,
        'sections_created': sections_created,
        'normalized': normalized,
    }
