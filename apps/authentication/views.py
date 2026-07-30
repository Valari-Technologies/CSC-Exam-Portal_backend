"""Authentication views — login, register, refresh, logout, profile, password flows, Google OAuth."""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.audit.models import AuditLog
from apps.audit.services import log_action

from .models import User
from .serializers import (
    ChangePasswordSerializer,
    GoogleLoginSerializer,
    LoginSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    SetupPasswordSerializer,
    StudentLoginSerializer,
    StudentPasswordResetRequestSerializer,
    UserSerializer,
)
from .services import (
    EmailDeliveryError,
    get_or_link_google_user,
    get_user_from_password_reset,
    issue_tokens_for_user,
    revoke_all_user_refresh_tokens,
    school_status_block_message,
    send_password_reset_email,
    verify_google_id_token,
)
from .throttles import (
    LoginRateThrottle,
    PasswordResetRateThrottle,
    PasswordSetupRateThrottle,
)

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(_request):
    """Lightweight health probe used by Docker healthcheck and load balancers."""
    return Response({'status': 'healthy', 'service': 'csc-exam-portal-backend'})


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        tokens = issue_tokens_for_user(user)
        log_action(user, AuditLog.Action.USER_CREATED, entity_type='User', entity_id=user.id, request=request)

        return Response(
            {
                **tokens,
                'user': UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            log_action(
                None,
                AuditLog.Action.LOGIN_FAILED,
                status=AuditLog.Status.FAILURE,
                request=request,
                details={'email': request.data.get('email', ''), 'errors': serializer.errors},
            )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user: User = serializer.validated_data['user']
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        tokens = issue_tokens_for_user(user)
        log_action(user, AuditLog.Action.LOGIN, entity_type='User', entity_id=user.id, request=request)

        return Response({**tokens, 'user': UserSerializer(user, context={"request": request}).data})


class StudentLoginView(APIView):
    """Student ID + password login, served at /api/v1/auth/student/login/.

    Kept separate from LoginView so the email+password path used by CSC Admin, School
    Admin and Teacher stays byte-for-byte unchanged. Throttling and audit logging
    mirror LoginView — a login route without them would be an un-audited,
    un-rate-limited way into the platform.
    """

    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = StudentLoginSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            log_action(
                None,
                AuditLog.Action.LOGIN_FAILED,
                status=AuditLog.Status.FAILURE,
                request=request,
                details={
                    'student_id': request.data.get('student_id', ''),
                    'method': 'student_id',
                    'errors': serializer.errors,
                },
            )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user: User = serializer.validated_data['user']
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        tokens = issue_tokens_for_user(user)
        log_action(
            user,
            AuditLog.Action.LOGIN,
            entity_type='User',
            entity_id=user.id,
            request=request,
            details={'method': 'student_id'},
        )

        return Response({**tokens, 'user': UserSerializer(user, context={"request": request}).data})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.validated_data['token_obj'].blacklist()
        except Exception:
            logger.warning('Failed to blacklist refresh token for user %s', request.user.id)

        log_action(request.user, AuditLog.Action.LOGOUT, entity_type='User', entity_id=request.user.id, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user, context={'request': request}).data)

    def put(self, request):
        return self._update(request, partial=False)

    def patch(self, request):
        return self._update(request, partial=True)

    def _update(self, request, *, partial: bool):
        serializer = UserSerializer(
            request.user, data=request.data, partial=partial, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        log_action(user, AuditLog.Action.USER_UPDATED, entity_type='User', entity_id=user.id, request=request)
        return Response(serializer.data)


class PasswordResetRequestView(APIView):
    """Always returns 200, regardless of whether the email exists, to prevent enumeration."""

    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRateThrottle]
    throttle_scope = 'password_reset'

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user:
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
            try:
                send_password_reset_email(user, frontend_url=frontend_url)
            except EmailDeliveryError:
                return Response(
                    {'detail': 'Email service is temporarily unavailable. '
                               'Please try again in a few minutes.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        return Response({'detail': 'If an account exists for that email, a reset link has been sent.'})


class StudentPasswordResetRequestView(APIView):
    """Password reset for students, who identify themselves by Student ID, not email.

    The Student ID resolves to the student's User, and the link goes to that user's own
    registered email. The school's official_email is never used — it is a contact address,
    not a credential.

    Always returns 200 so a Student ID cannot be probed for existence.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRateThrottle]
    throttle_scope = 'password_reset'

    def post(self, request):
        serializer = StudentPasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student_id = serializer.validated_data['student_id']

        user = User.objects.filter(
            student_id__iexact=student_id,
            role=User.Role.STUDENT,
            is_active=True,
        ).first()

        if user and user.email:
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
            try:
                send_password_reset_email(user, frontend_url=frontend_url)
            except EmailDeliveryError:
                return Response(
                    {'detail': 'Email service is temporarily unavailable. '
                               'Please try again in a few minutes.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        return Response(
            {'detail': 'If that Student ID exists, a reset link has been sent to the '
                       'email registered for that student.'}
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordSetupRateThrottle]
    throttle_scope = 'password_setup'

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = get_user_from_password_reset(
            serializer.validated_data['uid'],
            serializer.validated_data['token'],
        )
        if user is None:
            return Response(
                {'detail': 'Invalid or expired reset link.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data['new_password'])
        # Resetting via emailed link also satisfies the forced-change requirement —
        # otherwise the user would still be trapped behind the gate after resetting.
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password', 'updated_at'])
        revoked = revoke_all_user_refresh_tokens(user)
        log_action(
            user,
            AuditLog.Action.PASSWORD_RESET,
            entity_type='User',
            entity_id=user.id,
            request=request,
            details={'tokens_revoked': revoked},
        )
        return Response({'detail': 'Password reset successful. You can now log in.'})


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data['new_password'])
        # The temporary password has now been replaced — lift the forced-change gate.
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password', 'updated_at'])
        update_session_auth_hash(request, user)

        revoked = revoke_all_user_refresh_tokens(user)
        log_action(
            user,
            AuditLog.Action.PASSWORD_CHANGE,
            entity_type='User',
            entity_id=user.id,
            request=request,
            details={'tokens_revoked': revoked},
        )
        return Response({'detail': 'Password changed successfully.'})


class SetupPasswordView(APIView):
    """First-login password setup for provisioned accounts (teacher/student)."""

    permission_classes = [AllowAny]
    throttle_classes = [PasswordSetupRateThrottle]
    throttle_scope = 'password_setup'

    def post(self, request):
        serializer = SetupPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = get_user_from_password_reset(
            serializer.validated_data['uid'],
            serializer.validated_data['token'],
        )
        if user is None:
            return Response(
                {'detail': 'Invalid or expired setup link.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data['new_password'])
        user.is_password_set = True
        user.must_change_password = False
        user.save(update_fields=[
            'password', 'is_password_set', 'must_change_password', 'updated_at',
        ])
        revoked = revoke_all_user_refresh_tokens(user)
        log_action(
            user,
            AuditLog.Action.PASSWORD_CHANGE,
            entity_type='User',
            entity_id=user.id,
            request=request,
            details={'tokens_revoked': revoked},
        )

        tokens = issue_tokens_for_user(user)
        return Response({**tokens, 'user': UserSerializer(user, context={"request": request}).data})


class GoogleLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            profile = verify_google_id_token(serializer.validated_data['id_token'])
        except ValueError as exc:
            log_action(
                None,
                AuditLog.Action.LOGIN_FAILED,
                status=AuditLog.Status.FAILURE,
                request=request,
                details={'provider': 'google', 'reason': str(exc)},
            )
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = get_or_link_google_user(profile)
        if user is None:
            return Response(
                {'detail': (
                    'No account found for this Google identity. '
                    'Please ask your school admin to provision an account first.'
                )},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not user.is_active:
            return Response({'detail': 'Account is deactivated.'}, status=status.HTTP_403_FORBIDDEN)
        # Google is a third login door — it must honour school status like the other two,
        # or "Sign in with Google" would quietly bypass a suspension.
        blocked = school_status_block_message(user)
        if blocked:
            log_action(
                user,
                AuditLog.Action.LOGIN_FAILED,
                status=AuditLog.Status.FAILURE,
                request=request,
                details={'provider': 'google', 'reason': blocked},
            )
            return Response({'detail': blocked}, status=status.HTTP_403_FORBIDDEN)

        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])
        tokens = issue_tokens_for_user(user)
        log_action(
            user,
            AuditLog.Action.LOGIN,
            entity_type='User',
            entity_id=user.id,
            request=request,
            details={'provider': 'google'},
        )

        return Response({**tokens, 'user': UserSerializer(user, context={"request": request}).data})


# Re-export simplejwt's TokenRefreshView so the URL conf can import from one place.
__all__ = [
    'health_check',
    'RegisterView',
    'LoginView',
    'StudentLoginView',
    'LogoutView',
    'MeView',
    'PasswordResetRequestView',
    'StudentPasswordResetRequestView',
    'PasswordResetConfirmView',
    'ChangePasswordView',
    'GoogleLoginView',
    'TokenRefreshView',
]
