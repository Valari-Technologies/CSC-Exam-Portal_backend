from django.apps import AppConfig
from django.core.checks import register, Warning as DjangoWarning, Tags


def check_jwt_key_separation(app_configs, **kwargs):
    from django.conf import settings
    warnings = []
    if not settings.DEBUG:
        if getattr(settings, 'JWT_SIGNING_KEY', None) == settings.SECRET_KEY:
            warnings.append(
                DjangoWarning(
                    'JWT_SIGNING_KEY equals SECRET_KEY in production. '
                    'Set a distinct JWT_SIGNING_KEY in your environment.',
                    hint='Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"',
                    id='auth.W001',
                )
            )
    return warnings


class AuthenticationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.authentication'
    label = 'authentication'

    def ready(self):
        register(check_jwt_key_separation, Tags.security)
