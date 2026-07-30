from rest_framework.throttling import ScopedRateThrottle


class LoginRateThrottle(ScopedRateThrottle):
    """Throttle for the login endpoint — slow brute-force attacks."""
    scope = 'login'


class PasswordResetRateThrottle(ScopedRateThrottle):
    """Throttle for password reset requests — prevent reset spam."""
    scope = 'password_reset'


class PasswordSetupRateThrottle(ScopedRateThrottle):
    """Throttle for password setup/confirm endpoints."""
    scope = 'password_setup'
