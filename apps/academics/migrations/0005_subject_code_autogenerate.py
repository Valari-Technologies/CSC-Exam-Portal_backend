"""Repurpose Subject.code as the auto-generated Subject ID.

Backfills every existing subject with an ID of the form <school 2-letter prefix>_<subject
3-letter abbreviation>_<2-digit class> (e.g. KA_MAT_10), then adds a per-school uniqueness
constraint. The backfill is deterministic (subjects processed in creation order per school)
so collisions resolve to a stable _2/_3 suffix. Any value previously typed into `code` is
overwritten — the column now holds the generated ID.
"""
import re

from django.db import migrations, models
from django.db.models import Q

# Kept in sync with apps.academics.services.SUBJECT_PREFIX_MAP.
SUBJECT_PREFIX_MAP = {
    'mathematics': 'MAT',
    'maths': 'MAT',
    'english': 'ENG',
    'tamil': 'TAM',
    'science': 'SCI',
    'social science': 'SOC',
    'social': 'SOC',
}


def _school_prefix(code: str) -> str:
    letters = re.sub(r'[^A-Z]', '', (code or '').upper())
    return ((letters[:3] or 'SCH').ljust(3, 'X'))[:2]


def _subject_prefix(name: str) -> str:
    key = (name or '').strip().lower()
    if key in SUBJECT_PREFIX_MAP:
        return SUBJECT_PREFIX_MAP[key]
    letters = re.sub(r'[^A-Z]', '', (name or '').upper())
    return (letters[:3] or 'SUB').ljust(3, 'X')


def backfill_subject_codes(apps, schema_editor):
    Subject = apps.get_model('academics', 'Subject')
    School = apps.get_model('schools', 'School')

    for school in School.objects.all():
        used: set[str] = set()
        subjects = (
            Subject.objects.filter(school=school)
            .select_related('school_class')
            .order_by('created_at', 'id')
        )
        for subject in subjects:
            base = (
                f'{_school_prefix(school.code)}_'
                f'{_subject_prefix(subject.name)}_'
                f'{subject.school_class.numeric_value:02d}'
            )
            code = base
            suffix = 2
            while code in used:
                code = f'{base}_{suffix}'
                suffix += 1
            used.add(code)
            if subject.code != code:
                subject.code = code
                subject.save(update_fields=['code'])


def clear_subject_codes(apps, schema_editor):
    Subject = apps.get_model('academics', 'Subject')
    Subject.objects.update(code='')


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0004_narrow_sections_to_a_f'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subject',
            name='code',
            field=models.CharField(
                blank=True,
                help_text='Subject ID — auto-generated (e.g. KA_MAT_10). Not editable.',
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_subject_codes, clear_subject_codes),
        migrations.AddConstraint(
            model_name='subject',
            constraint=models.UniqueConstraint(
                condition=~Q(code=''),
                fields=('school', 'code'),
                name='unique_subject_code_per_school',
            ),
        ),
    ]
