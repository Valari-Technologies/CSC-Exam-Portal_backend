import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class PasswordComplexityValidator:
    """Requires a mix of uppercase, lowercase, digit, and special char."""

    SPECIAL_CHARS = r'!@#$%^&*()_+\-=\[\]{};:,.<>?/\\|`~"\''

    def validate(self, password, user=None):
        errors = []
        if not re.search(r'[A-Z]', password):
            errors.append(_('Password must contain at least one uppercase letter.'))
        if not re.search(r'[a-z]', password):
            errors.append(_('Password must contain at least one lowercase letter.'))
        if not re.search(r'\d', password):
            errors.append(_('Password must contain at least one digit.'))
        if not re.search(f'[{re.escape(self.SPECIAL_CHARS)}]', password):
            errors.append(_(
                'Password must contain at least one special character '
                'from: !@#$%^&*()_+-=[]{};:,.<>?'
            ))
        if errors:
            raise ValidationError(errors, code='password_complexity')

    def get_help_text(self):
        return _(
            'Your password must contain at least one uppercase letter, '
            'one lowercase letter, one digit, and one special character.'
        )
