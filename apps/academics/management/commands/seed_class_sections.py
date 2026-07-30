"""Seed standard Classes (1st-10th) and Sections (A-H) for schools.

Idempotent: re-running makes no duplicate rows. Existing Class rows whose
`numeric_value` does not match their ordinal name are normalized so the
add-student dropdown labels them correctly.

Usage:
    python manage.py seed_class_sections --all
    python manage.py seed_class_sections --school 3
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.services import seed_class_sections_for_school
from apps.schools.models import School


class Command(BaseCommand):
    help = 'Ensure each school has Classes 1st-10th and Sections A-H (idempotent).'

    def add_arguments(self, parser) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--school', type=int, help='School id to seed.')
        group.add_argument('--all', action='store_true', help='Seed all schools.')

    def handle(self, *args, **options) -> None:
        if options['all']:
            schools = list(School.objects.all().order_by('id'))
        else:
            schools = list(School.objects.filter(pk=options['school']))
            if not schools:
                raise CommandError(f"School id={options['school']} not found.")

        for school in schools:
            with transaction.atomic():
                counts = seed_class_sections_for_school(school)
            self.stdout.write(self.style.SUCCESS(
                f'School {school.id} "{school.name}": '
                f'+{counts["classes_created"]} classes, '
                f'+{counts["sections_created"]} sections, '
                f'{counts["normalized"]} class names normalized.'
            ))
