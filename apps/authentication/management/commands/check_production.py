"""Pre-deployment validation command.

Run on the production server BEFORE applying migrations:
    python manage.py check_production

Exit code 0 = ready to deploy, 1 = NOT ready.
"""
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Validate production-readiness. Returns non-zero exit on any failure.'

    def handle(self, *args, **options):
        checks = []

        # Check 1: DEBUG must be False
        checks.append(self._check(
            'DEBUG is False',
            settings.DEBUG is False,
            f'DEBUG={settings.DEBUG} — must be False in production',
        ))

        # Check 2: SECRET_KEY not a default/placeholder
        sk = settings.SECRET_KEY or ''
        checks.append(self._check(
            'SECRET_KEY is not a placeholder',
            len(sk) >= 50 and not sk.startswith(('dev-secret-key', 'change-me')),
            'SECRET_KEY is too short or still set to a default placeholder',
        ))

        # Check 3: JWT_SIGNING_KEY exists AND differs from SECRET_KEY
        jsk = getattr(settings, 'JWT_SIGNING_KEY', '')
        checks.append(self._check(
            'JWT_SIGNING_KEY is set and distinct from SECRET_KEY',
            bool(jsk) and jsk != settings.SECRET_KEY,
            'JWT_SIGNING_KEY either missing or equals SECRET_KEY',
        ))

        # Check 4: ALLOWED_HOSTS does not contain wildcard
        hosts = settings.ALLOWED_HOSTS or []
        checks.append(self._check(
            'ALLOWED_HOSTS has no wildcards',
            '*' not in hosts,
            'ALLOWED_HOSTS contains "*" — must list specific hostnames',
        ))

        # Check 5: FRONTEND_URL is https://
        fu = getattr(settings, 'FRONTEND_URL', '')
        checks.append(self._check(
            'FRONTEND_URL uses HTTPS',
            fu.startswith('https://'),
            f'FRONTEND_URL="{fu}" — must start with https:// in production',
        ))

        # Check 6: EMAIL_HOST is set and non-default
        eh = getattr(settings, 'EMAIL_HOST', '')
        checks.append(self._check(
            'EMAIL_HOST is configured',
            bool(eh),
            'EMAIL_HOST is empty — outbound mail will fail',
        ))

        # Check 7: EMAIL_HOST_PASSWORD is set
        ehp = getattr(settings, 'EMAIL_HOST_PASSWORD', '')
        checks.append(self._check(
            'EMAIL_HOST_PASSWORD is set',
            bool(ehp),
            'EMAIL_HOST_PASSWORD is empty — SMTP auth will fail',
        ))

        # Check 8: Database is not SQLite
        db_engine = settings.DATABASES.get('default', {}).get('ENGINE', '')
        checks.append(self._check(
            'Database backend is not SQLite',
            'sqlite' not in db_engine.lower(),
            f'Database ENGINE="{db_engine}" — use PostgreSQL in production',
        ))

        # Check 9: SECURE_SSL_REDIRECT enabled
        checks.append(self._check(
            'SECURE_SSL_REDIRECT is enabled',
            getattr(settings, 'SECURE_SSL_REDIRECT', False),
            'SECURE_SSL_REDIRECT is False — HTTPS will not be enforced',
        ))

        # Check 10: SESSION_COOKIE_SECURE enabled
        checks.append(self._check(
            'SESSION_COOKIE_SECURE is True',
            getattr(settings, 'SESSION_COOKIE_SECURE', False),
            'SESSION_COOKIE_SECURE is False — session cookie exposed over HTTP',
        ))

        # Print results
        self.stdout.write('')
        self.stdout.write('=' * 60)
        self.stdout.write(self.style.HTTP_INFO(' PRODUCTION READINESS CHECK '))
        self.stdout.write('=' * 60)

        for label, passed, message in checks:
            if passed:
                self.stdout.write(f'  {self.style.SUCCESS("PASS")}  {label}')
            else:
                self.stdout.write(f'  {self.style.ERROR("FAIL")}  {label}')
                self.stdout.write(f'         -> {message}')

        self.stdout.write('=' * 60)

        failures = sum(1 for _, p, _ in checks if not p)
        if failures:
            self.stdout.write(self.style.ERROR(
                f' {failures} of {len(checks)} checks FAILED. NOT ready to deploy.'
            ))
            self.stdout.write('=' * 60)
            exit(1)

        self.stdout.write(self.style.SUCCESS(
            f' All {len(checks)} checks PASSED. Ready to deploy.'
        ))
        self.stdout.write('=' * 60)

    def _check(self, label, passed, failure_message):
        return (label, passed, failure_message)
