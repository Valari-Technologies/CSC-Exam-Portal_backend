"""Narrow the standard section range from A-H to A-F.

Sections G and H are no longer part of the seeded layout. This removes the leftover ones,
but ONLY where nothing references them: a section that has students, teacher assignments,
or test assignments is real data, and deleting it would take those with it (StudentProfile
and TeacherAssignment cascade off Section). Any such section is kept and reported, so a
restored or divergent database degrades to "G/H still exist" rather than losing rows.
"""
from django.db import migrations

REMOVED_SECTIONS = ['G', 'H']


def remove_unused_gh_sections(apps, schema_editor):
    Section = apps.get_model('academics', 'Section')
    StudentProfile = apps.get_model('students', 'StudentProfile')
    TeacherAssignment = apps.get_model('teachers', 'TeacherAssignment')
    TestAssignment = apps.get_model('tests', 'TestAssignment')

    candidates = Section.objects.filter(name__in=REMOVED_SECTIONS)
    in_use_ids = set()
    for model, field in (
        (StudentProfile, 'section_id'),
        (TeacherAssignment, 'section_id'),
        (TestAssignment, 'section_id'),
    ):
        in_use_ids.update(
            model.objects.filter(**{f'{field}__in': candidates.values('id')})
            .values_list(field, flat=True)
        )

    deletable = candidates.exclude(id__in=in_use_ids)
    deleted_count = deletable.count()
    deletable.delete()

    if in_use_ids:
        print(
            f'\n  Kept {len(in_use_ids)} section(s) named G/H that are still in use; '
            f'removed {deleted_count} unused.'
        )


def restore_gh_sections(apps, schema_editor):
    """Reverse: recreate G/H for every class, matching the old A-H layout."""
    Section = apps.get_model('academics', 'Section')
    Class = apps.get_model('academics', 'Class')

    for school_class in Class.objects.all().iterator():
        for name in REMOVED_SECTIONS:
            Section.objects.get_or_create(
                school_id=school_class.school_id,
                school_class=school_class,
                name=name,
                defaults={'is_active': True},
            )


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0003_limit_classes_to_grade_10'),
        ('students', '0001_initial'),
        ('teachers', '0001_initial'),
        ('tests', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(remove_unused_gh_sections, restore_gh_sections),
    ]
