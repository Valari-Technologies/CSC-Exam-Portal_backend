"""Development settings."""
from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ['*']

CORS_ALLOW_ALL_ORIGINS = True

# Send real mail in development ONLY when SMTP credentials are actually configured;
# otherwise fall back to printing to the console (the convenient default for local work).
# Without this, password-setup links were only ever printed and never delivered, even with
# EMAIL_HOST_USER/EMAIL_HOST_PASSWORD set — this line hard-forced the console backend.
EMAIL_BACKEND = (
    'django.core.mail.backends.smtp.EmailBackend'
    if EMAIL_HOST and EMAIL_HOST_USER  # noqa: F405
    else 'django.core.mail.backends.console.EmailBackend'
)

# Permissive throttling in dev
REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] = {  # noqa: F405
    'anon': '1000/minute',
    'user': '10000/hour',
    'login': '1000/minute',
    'password_reset': '1000/hour',
    'password_setup': '1000/hour',
}

# CSP in development: report-only mode so violations log to
# browser console without breaking page loads.
CSP_REPORT_ONLY = True

# Match production CSP shape so dev catches violations early.
CSP_DEFAULT_SRC = ("'self'", "'unsafe-eval'", "'unsafe-inline'")  # vite HMR needs these
CSP_SCRIPT_SRC = ("'self'", "'unsafe-eval'", "'unsafe-inline'")
CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")
CSP_CONNECT_SRC = ("'self'", 'ws:', 'http://localhost:*')
