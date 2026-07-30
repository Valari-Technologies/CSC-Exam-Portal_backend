"""Change a CSC Admin (Super Admin) login email.

Super Admin accounts are provisioned via `createsuperuser`, never through a public
sign-up route — self-service registration of the platform's highest-privilege role
would let anyone grant themselves full access. This command is the safe way to move
an existing Super Admin off a placeholder email (e.g. admin@test.com) onto a real
address, runnable only by whoever controls the server.

Usage:
    python manage.py change_admin_email --new you@example.com
    python manage.py change_admin_email --current admin@test.com --new you@example.com
    python manage.py change_admin_email --new you@example.com --no-input

The email is the login username, so changing it invalidates outstanding refresh
tokens: the admin must sign in again with the new address.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.authentication.services import revoke_all_user_refresh_tokens

User = get_user_model()


class Command(BaseCommand):
    help = 'Change the login email of a CSC Admin (Super Admin) account.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--new',
            required=True,
            help='The new login email for the Super Admin.',
        )
        parser.add_argument(
            '--current',
            default=None,
            help='Current email of the Super Admin to change. '
                 'Required only when more than one CSC Admin exists.',
        )
        parser.add_argument(
            '--no-input',
            action='store_true',
            help='Skip the confirmation prompt (for scripted runs).',
        )

    def handle(self, *args, **options):
        admin = self._resolve_admin(options.get('current'))
        old_email = admin.email

        new_email = self._clean_new_email(options['new'], exclude_id=admin.id)
        if old_email and old_email.lower() == new_email.lower():
            raise CommandError(
                f'The Super Admin already uses {new_email}. Nothing to change.'
            )

        self.stdout.write('')
        self.stdout.write(self.style.HTTP_INFO(' CHANGE SUPER ADMIN EMAIL '))
        self.stdout.write(f'  Account : {admin.full_name} (id={admin.id}, role={admin.role})')
        self.stdout.write(f'  From    : {old_email}')
        self.stdout.write(f'  To      : {new_email}')
        self.stdout.write('')

        if not options['no_input']:
            answer = input('Apply this change? [y/N] ').strip().lower()
            if answer not in ('y', 'yes'):
                self.stdout.write(self.style.WARNING('Aborted. No changes made.'))
                return

        with transaction.atomic():
            admin.email = new_email
            admin.save(update_fields=['email', 'updated_at'])
            revoked = revoke_all_user_refresh_tokens(admin)
            log_action(
                admin,
                AuditLog.Action.USER_UPDATED,
                entity_type='User',
                entity_id=admin.id,
                details={
                    'field': 'email',
                    'old_email': old_email,
                    'new_email': new_email,
                    'via': 'change_admin_email command',
                    'tokens_revoked': revoked,
                },
            )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. Super Admin now signs in with {new_email}.'
        ))
        self.stdout.write(
            f'  {revoked} refresh token(s) revoked — any active session must log in again.'
        )
        self.stdout.write(
            '  The password is unchanged. Use "Forgot password" on the login page '
            'if you also want a reset link sent to the new address.'
        )

    def _clean_new_email(self, raw: str, *, exclude_id: int) -> str:
        email = (raw or '').strip()
        try:
            validate_email(email)
        except DjangoValidationError:
            raise CommandError(f'"{raw}" is not a valid email address.')
        # Normalize to lower-case host like the auth serializers do, so login
        # (which lower-cases the entered email) always matches.
        email = User.objects.normalize_email(email).lower()

        clash = User.objects.filter(email__iexact=email).exclude(id=exclude_id).first()
        if clash is not None:
            raise CommandError(
                f'{email} is already in use by another account '
                f'(id={clash.id}, role={clash.role}). Choose a different email.'
            )
        return email

    def _resolve_admin(self, current: str | None) -> User:
        admins = User.objects.filter(role=User.Role.CSC_ADMIN)

        if current:
            admin = admins.filter(email__iexact=current.strip()).first()
            if admin is None:
                raise CommandError(
                    f'No CSC Admin found with email {current}.'
                )
            return admin

        count = admins.count()
        if count == 0:
            raise CommandError(
                'No CSC Admin account exists. Create one with '
                '"python manage.py createsuperuser" first.'
            )
        if count > 1:
            emails = ', '.join(a.email or f'(no email, id={a.id})' for a in admins)
            raise CommandError(
                f'{count} CSC Admin accounts exist ({emails}). '
                f'Pass --current to say which one to change.'
            )
        return admins.first()
