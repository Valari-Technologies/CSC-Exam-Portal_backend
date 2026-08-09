"""Production settings."""
from .base import *  # noqa: F401, F403

import os

_REQUIRED_PRODUCTION_ENV_VARS = [
    'SECRET_KEY',
    'JWT_SIGNING_KEY',
    'FRONTEND_URL',
    'EMAIL_HOST',
    # EMAIL_HOST_USER is the SMTP login. Gmail (and every authenticated SMTP
    # relay) rejects the session without it, so a deploy that sets the password
    # but not the user silently fails to send every email — password resets and
    # account-setup links alike. Require it so the misconfiguration surfaces at
    # startup instead of as "the reset email never arrives".
    'EMAIL_HOST_USER',
    'EMAIL_HOST_PASSWORD',
    'ALLOWED_HOSTS',
]

_missing = [
    var for var in _REQUIRED_PRODUCTION_ENV_VARS
    if not os.environ.get(var)
]
if _missing:
    if os.environ.get('VERCEL') == '1':
        print(
            'WARNING: The following required environment variables are missing '
            'for production: ' + ', '.join(_missing)
        )
    else:
        raise RuntimeError(
            'The following required environment variables are missing '
            'for production: ' + ', '.join(_missing) + '. '
            'Set them in your environment before starting the server.'
        )

# Reject dev defaults that may have leaked into production env.
if os.environ.get('SECRET_KEY', '').startswith('dev-secret-key'):
    if os.environ.get('VERCEL') == '1':
        print('WARNING: SECRET_KEY is still set to the development default.')
    else:
        raise RuntimeError(
            'SECRET_KEY is still set to the development default. '
            'Generate a strong key: python -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
if os.environ.get('SECRET_KEY', '').startswith('change-me'):
    if os.environ.get('VERCEL') == '1':
        print('WARNING: SECRET_KEY is still the placeholder.')
    else:
        raise RuntimeError(
            'SECRET_KEY is still the placeholder from .env.example. '
            'Generate a real key.'
        )

DEBUG = False

# Production must send real emails via SMTP.
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# === Sentry — optional error tracking ===
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[
                DjangoIntegration(),
                LoggingIntegration(level=None, event_level=None),
            ],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
            environment=SENTRY_ENVIRONMENT,
        )
    except ImportError:
        # sentry-sdk not installed — skip silently. Production should
        # have it in requirements.txt; dev/CI can run without it.
        pass

# === Content Security Policy ===
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = (
    "'self'",
    'https://accounts.google.com',
    'https://apis.google.com',
)
CSP_STYLE_SRC = (
    "'self'",
    "'unsafe-inline'",  # Tailwind generates inline styles
)
CSP_IMG_SRC = ("'self'", 'data:', 'https:')
CSP_FONT_SRC = ("'self'", 'data:')
CSP_CONNECT_SRC = ("'self'",)
CSP_FRAME_ANCESTORS = ("'none'",)
CSP_FORM_ACTION = ("'self'",)
CSP_FRAME_SRC = ('https://accounts.google.com',)
CSP_BASE_URI = ("'self'",)
CSP_OBJECT_SRC = ("'none'",)

if CSP_REPORT_URI:
    CSP_REPORT_URI_VALUE = (CSP_REPORT_URI,)

# Remove deprecated XSS filter header — modern browsers ignore it
# and it can introduce vulnerabilities in legacy browsers.
SECURE_BROWSER_XSS_FILTER = False

# Security
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = os.environ.get('VERCEL') != '1'
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# Use environment-driven allowed hosts only (no wildcards)
# ALLOWED_HOSTS already comes from env in base.py

# Automatically allow Vercel URLs when running on Vercel
if os.environ.get('VERCEL') == '1':
    ALLOWED_HOSTS = ['.vercel.app', 'localhost', '127.0.0.1']
    if os.environ.get('ALLOWED_HOSTS'):
        for host in os.environ.get('ALLOWED_HOSTS').split(','):
            h = host.strip()
            if h and h not in ALLOWED_HOSTS:
                ALLOWED_HOSTS.append(h)
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ORIGIN_ALLOW_ALL = True



