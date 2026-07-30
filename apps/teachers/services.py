"""Teacher creation + assignment helpers."""
from __future__ import annotations

import re

from apps.schools.models import School
from apps.schools.services import school_letter_prefix

from .models import TeacherProfile

TEACHER_ID_SEQUENCE_WIDTH = 3
TEACHER_ID_MARKER = 'TR'


def generate_teacher_id(school: School) -> str:
    """Next free Teacher ID for this school, e.g. 'KAR_TR_001'.

    Format: the school's three-letter prefix, an underscore, TR to mark a teacher, another
    underscore, then a zero-padded sequence. The sequence runs independently per school, so
    two schools sharing a prefix each start at 001 — Teacher IDs are unique *within* a school
    (teachers log in by email, not by this ID), which is why the DB constraint is scoped to
    the school.
    """
    prefix = f'{school_letter_prefix(school)}_{TEACHER_ID_MARKER}_'

    pattern = re.compile(rf'^{re.escape(prefix)}(\d+)$')
    highest = 0
    for value in TeacherProfile.objects.filter(
        school=school, teacher_id__startswith=prefix,
    ).values_list('teacher_id', flat=True):
        match = pattern.match(value)
        if match:
            highest = max(highest, int(match.group(1)))

    # Walk forward until the ID is genuinely free — guards against an ID assigned manually
    # or by an older scheme that skipped ahead of the sequence.
    candidate_number = highest + 1
    while True:
        candidate = f'{prefix}{candidate_number:0{TEACHER_ID_SEQUENCE_WIDTH}d}'
        if not TeacherProfile.objects.filter(school=school, teacher_id=candidate).exists():
            return candidate
        candidate_number += 1
