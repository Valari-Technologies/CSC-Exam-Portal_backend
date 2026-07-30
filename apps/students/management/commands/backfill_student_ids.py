"""Assign a Student ID to every student created before Student-ID login existed.

Idempotent: students that already have an ID are skipped, so this is safe to re-run.
Passwords are untouched — an existing student keeps whatever password they had and can
sign in at /studentlogin with their new ID immediately.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.students.services import generate_student_id

User = get_user_model()


class Command(BaseCommand):
    help = 'Generate Student IDs for existing students that do not have one.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be assigned without writing to the database.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']

        students = (
            User.objects.filter(role=User.Role.STUDENT, student_id__isnull=True)
            .select_related('school')
            .order_by('school_id', 'id')
        )

        if not students.exists():
            self.stdout.write(
                self.style.SUCCESS('All students already have a Student ID. Nothing to do.')
            )
            return

        assigned = 0
        skipped = 0
        for student in students:
            if student.school is None:
                self.stdout.write(
                    self.style.WARNING(
                        f'SKIP  {student.email} — student has no school, cannot build an ID prefix.'
                    )
                )
                skipped += 1
                continue

            # Always write, even on a dry run: generate_student_id() derives the next
            # free number by querying saved IDs, so skipping the save would hand every
            # student in a school the same ID and print a fiction. The whole command
            # runs in one transaction, rolled back below when --dry-run is set.
            student.student_id = generate_student_id(student.school)
            student.save(update_fields=['student_id'])
            self.stdout.write(f'{student.student_id}  <-  {student.email} ({student.school.name})')
            assigned += 1

        verb = 'Would assign' if dry_run else 'Assigned'
        self.stdout.write(
            self.style.SUCCESS(f'\n{verb} {assigned} Student ID(s). Skipped {skipped}.')
        )

        if dry_run:
            # Roll back so a dry run can never leave anything behind.
            transaction.set_rollback(True)
