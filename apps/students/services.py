"""Student ID and initial-password generation.

Students sign in at /studentlogin with a Student ID instead of an email, so every
student needs a globally-unique ID and a real password from the moment they are
created (there is no emailed setup step for them).
"""
from __future__ import annotations

import re
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model

from apps.schools.models import School
from apps.schools.services import school_letter_prefix

User = get_user_model()

# Ambiguous glyphs (0/O, 1/l/I) are excluded — these credentials get printed and
# read off paper by children, and a misread character is a support call.
_PASSWORD_UPPER = 'ABCDEFGHJKMNPQRSTUVWXYZ'
_PASSWORD_LOWER = 'abcdefghijkmnpqrstuvwxyz'
_PASSWORD_DIGITS = '23456789'
_PASSWORD_SPECIAL = '!@#$%^&*'

# Generated passwords are exactly: 1 uppercase + 1 digit + 1 special + the rest lowercase.
# At the default length of 8 that is 1/5/1/1. Length is settings.PASSWORD_MIN_LENGTH so a
# generated password can never be shorter than the policy demands.
_PASSWORD_UPPER_COUNT = 1
_PASSWORD_DIGIT_COUNT = 1
_PASSWORD_SPECIAL_COUNT = 1
_PASSWORD_FIXED_COUNT = _PASSWORD_UPPER_COUNT + _PASSWORD_DIGIT_COUNT + _PASSWORD_SPECIAL_COUNT

STUDENT_ID_SEQUENCE_WIDTH = 3
STUDENT_ID_MARKER = 'ST'


def generate_student_id(school: School) -> str:
    """Next free Student ID for this school, e.g. 'KAR_ST_001'.

    Format: the school's three-letter prefix, an underscore, ST to mark a student, another
    underscore, then a zero-padded sequence (KAR_ST_001, KAR_ST_002...). The prefix comes
    from the shared ``school_letter_prefix`` so Student and Teacher IDs agree.

    Unlike Teacher IDs, Student IDs are the *login credential* and the student login form has
    no school selector, so they must be unique *globally*, not just per school. The sequence
    scan below is per-prefix (so a school's numbering starts at 001), but the final existence
    check is GLOBAL and unfiltered — a `student_id` is `unique=True` at the DB level. If two
    schools ever shared a three-letter prefix, the second school's numbering would continue
    past the first's rather than collide: login stays correct, at the cost of that school not
    restarting at 001. Keep this guard global.
    """
    prefix = f'{school_letter_prefix(school)}_{STUDENT_ID_MARKER}_'

    pattern = re.compile(rf'^{re.escape(prefix)}(\d+)$')
    highest = 0
    for value in User.objects.filter(
        student_id__startswith=prefix,
    ).values_list('student_id', flat=True):
        match = pattern.match(value)
        if match:
            highest = max(highest, int(match.group(1)))

    # Walk forward until the ID is genuinely free — GLOBAL check (no school filter), because
    # student_id is globally unique. Also guards against IDs from an older scheme.
    candidate_number = highest + 1
    while True:
        candidate = f'{prefix}{candidate_number:0{STUDENT_ID_SEQUENCE_WIDTH}d}'
        if not User.objects.filter(student_id=candidate).exists():
            return candidate
        candidate_number += 1


def generate_student_password(length: int | None = None) -> str:
    """Random password with exactly the policy's composition.

    At the default length of 8: 1 uppercase, 5 lowercase, 1 digit, 1 special. The counts
    are exact rather than "at least one of each" so every generated password matches the
    agreed shape; the remainder after the three fixed characters is lowercase.

    Returned in plaintext exactly once, at creation, so the admin can hand it to the
    student. It is never persisted — only Django's salted hash is stored.
    """
    if length is None:
        length = settings.PASSWORD_MIN_LENGTH
    if length < _PASSWORD_FIXED_COUNT + 1:
        raise ValueError(f'Password length must be at least {_PASSWORD_FIXED_COUNT + 1}.')

    chars = [secrets.choice(_PASSWORD_UPPER) for _ in range(_PASSWORD_UPPER_COUNT)]
    chars += [secrets.choice(_PASSWORD_DIGITS) for _ in range(_PASSWORD_DIGIT_COUNT)]
    chars += [secrets.choice(_PASSWORD_SPECIAL) for _ in range(_PASSWORD_SPECIAL_COUNT)]
    # Everything left over is lowercase — 5 of them at the default length.
    chars += [secrets.choice(_PASSWORD_LOWER) for _ in range(length - _PASSWORD_FIXED_COUNT)]

    rng = secrets.SystemRandom()
    rng.shuffle(chars)
    return ''.join(chars)
