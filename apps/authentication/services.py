"""Authentication services.

Centralises token issuance, password-reset token generation/verification,
and Google OAuth id_token verification so views stay thin.
"""
from __future__ import annotations

import logging
from typing import Optional, TypedDict

import requests
from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.tokens import RefreshToken as SimpleJWTRefreshToken

from .models import User

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an outgoing email cannot be delivered."""


def school_status_block_message(user: User) -> Optional[str]:
    """Why this user's school bars them from signing in, or None if it doesn't.

    A School's status is the CSC Admin's lever over that school: if suspending a school
    still let everyone in, the flag would be decorative. Both `inactive` and `suspended`
    stop every member — admin, teacher and student alike.

    CSC Admins have no school (`school` is null) and are never blocked; they must stay able
    to sign in to lift the suspension.

    Returned rather than raised so every login door can answer in its own idiom (the
    password paths raise a serializer error; the Google view returns a Response). Every
    caller has already proven the credential by this point, so naming the reason leaks
    nothing the person doesn't already know — and telling them "contact CSC" beats an
    unexplained failure.
    """
    # Imported lazily: apps.schools imports back into authentication, so a module-level
    # import here would be circular.
    from apps.schools.models import School

    school = user.school
    if school is None or school.status == School.Status.ACTIVE:
        return None
    if school.status == School.Status.SUSPENDED:
        return 'This school has been suspended. Please contact CSC support.'
    return 'This school is inactive. Please contact CSC support.'


GOOGLE_TOKENINFO_URL = 'https://oauth2.googleapis.com/tokeninfo'

password_reset_token_generator = PasswordResetTokenGenerator()


class TokenPair(TypedDict):
    access: str
    refresh: str


def issue_tokens_for_user(user: User) -> TokenPair:
    """Issue a fresh JWT access + refresh pair and embed role/school claims."""
    refresh = SimpleJWTRefreshToken.for_user(user)
    refresh['role'] = user.role
    refresh['school_id'] = user.school_id
    refresh['email'] = user.email

    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }


# ---------- Password reset ----------

def build_password_reset_uid_token(user: User) -> tuple[str, str]:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = password_reset_token_generator.make_token(user)
    return uid, token


def get_user_from_password_reset(uid: str, token: str) -> Optional[User]:
    try:
        user_id = force_str(urlsafe_base64_decode(uid))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    if not password_reset_token_generator.check_token(user, token):
        return None
    return user


def send_password_reset_email(user: User, *, frontend_url: str) -> None:
    """Send a password reset email. In DEBUG, this just logs the link to the console."""
    uid, token = build_password_reset_uid_token(user)
    reset_link = f'{frontend_url.rstrip("/")}/reset-password?uid={uid}&token={token}'
    subject = 'Reset your CSC Exam Portal password'
    message = (
        f'Hi {user.full_name},\n\n'
        'We received a request to reset your password. Click the link below to set a new one. '
        'If you did not request this, you can safely ignore this email.\n\n'
        f'{reset_link}\n\n'
        'This link expires in 1 hour.\n\n'
        '— CSC Exam Portal'
    )

    if settings.DEBUG:
        logger.info('Password reset link for %s: %s', user.email, reset_link)

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@csc-exam-portal.local'),
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.exception('Failed to send password reset email to %s', user.email)
        if not settings.DEBUG:
            raise EmailDeliveryError(
                f'Failed to send password reset email to {user.email}'
            ) from exc



def build_password_setup_link(user: User, *, frontend_url: str) -> str:
    """Build the one-time password-setup URL for a provisioned account.

    The link is valid independent of whether the notification email is delivered, so a
    caller can surface it directly (e.g. to a CSC Admin) even when email is unavailable.
    """
    uid, token = build_password_reset_uid_token(user)
    return f'{frontend_url.rstrip("/")}/setup-password?uid={uid}&token={token}'


def send_password_setup_email(user: User, *, frontend_url: str) -> str:
    """Send a password setup email for newly provisioned accounts.

    Returns the setup link so the caller can also display it (the link works whether or
    not delivery succeeds).
    """
    setup_link = build_password_setup_link(user, frontend_url=frontend_url)
    subject = 'Set up your CSC Exam Portal password'
    message = (
        f'Hi {user.full_name},\n\n'
        f'An account has been created for you on the CSC Exam Portal as a {user.get_role_display()}.\n\n'
        'Click the link below to set your password and start using the platform:\n\n'
        f'{setup_link}\n\n'
        'This link expires in 1 hour.\n\n'
        '— CSC Exam Portal'
    )

    if settings.DEBUG:
        logger.info('Password setup link for %s: %s', user.email, setup_link)

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@csc-exam-portal.local'),
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.exception('Failed to send password setup email to %s', user.email)
        if not settings.DEBUG:
            raise EmailDeliveryError(
                f'Failed to send password setup email to {user.email}'
            ) from exc


    return setup_link


# ---------- Google OAuth ----------

class GoogleProfile(TypedDict):
    sub: str
    email: str
    email_verified: bool
    name: str
    picture: Optional[str]


def verify_google_id_token(id_token: str) -> GoogleProfile:
    """Verify a Google id_token via Google's tokeninfo endpoint.

    Raises ValueError on any failure: invalid token, wrong audience, unverified email.
    """
    response = requests.get(
        GOOGLE_TOKENINFO_URL,
        params={'id_token': id_token},
        timeout=10,
    )
    if response.status_code != 200:
        raise ValueError('Invalid Google token.')

    data = response.json()

    expected_aud = getattr(settings, 'GOOGLE_CLIENT_ID', '') or \
        settings.SOCIALACCOUNT_PROVIDERS.get('google', {}).get('APP', {}).get('client_id', '')

    # FAIL CLOSED. This check used to be skipped whenever GOOGLE_CLIENT_ID was unset, which
    # meant an unconfigured deployment accepted a valid Google id_token issued to ANY other
    # Google app — letting anyone sign in as any user whose email they knew. Without a
    # configured audience there is no way to prove the token was minted for us, so refuse.
    if not expected_aud:
        raise ValueError('Google sign-in is not configured on this server.')

    if data.get('aud') != expected_aud:
        raise ValueError('Token audience mismatch.')

    # The token must actually come from Google (tokeninfo should guarantee it; asserting the
    # issuer as well costs nothing and is the standard OAuth 2.0 / OIDC check).
    if data.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise ValueError('Untrusted token issuer.')

    if data.get('email_verified') not in (True, 'true'):
        raise ValueError('Email is not verified by Google.')

    return GoogleProfile(
        sub=data['sub'],
        email=data['email'].lower(),
        email_verified=True,
        name=data.get('name', data.get('email', '').split('@')[0]),
        picture=data.get('picture'),
    )


def get_or_link_google_user(profile: GoogleProfile) -> Optional[User]:
    """Look up an existing user by oauth_id or email and link the Google identity.

    Returns the user if found/linked; returns None if no account exists.
    For MVP, we do NOT auto-create accounts on Google login — users must be
    provisioned first by their school admin.
    """
    user = User.objects.filter(oauth_provider='google', oauth_id=profile['sub']).first()
    if user:
        return user

    user = User.objects.filter(email__iexact=profile['email']).first()
    if user:
        user.oauth_provider = 'google'
        user.oauth_id = profile['sub']
        if not user.profile_picture and profile.get('picture'):
            user.profile_picture = profile['picture']
        user.save(update_fields=['oauth_provider', 'oauth_id', 'profile_picture', 'updated_at'])
        return user

    return None


# ---------- Token revocation ----------

def revoke_all_user_refresh_tokens(user: User) -> int:
    """Blacklist all outstanding refresh tokens for the user.

    Returns the count of tokens newly blacklisted (not already in
    the blacklist).
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )
    outstanding = OutstandingToken.objects.filter(user=user)
    count = 0
    for token in outstanding:
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        if created:
            count += 1
    return count
