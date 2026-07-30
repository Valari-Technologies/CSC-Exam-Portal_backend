"""Authentication URL routes — all endpoints live under /api/v1/auth/."""
from django.urls import path

from .views import (
    ChangePasswordView,
    GoogleLoginView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    SetupPasswordView,
    StudentLoginView,
    StudentPasswordResetRequestView,
    TokenRefreshView,
    health_check,
)

app_name = 'authentication'

urlpatterns = [
    path('health/', health_check, name='health'),

    # Self-registration is disabled — admin accounts are provisioned out-of-band
    # and users set their password via the Forgot Password flow.
    # Email + password — CSC Admin, School Admin, Teacher.
    path('login/', LoginView.as_view(), name='login'),
    # Student ID + password — students only (frontend: /studentlogin).
    path('student/login/', StudentLoginView.as_view(), name='student-login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('refresh/', TokenRefreshView.as_view(), name='refresh'),
    path('me/', MeView.as_view(), name='me'),

    # Staff reset by login email; students reset by Student ID (the link still goes to
    # the student's own registered email — never to the school's official_email).
    path('password/reset/', PasswordResetRequestView.as_view(), name='password-reset'),
    path('password/reset/student/', StudentPasswordResetRequestView.as_view(),
         name='password-reset-student'),
    path('password/confirm/', PasswordResetConfirmView.as_view(), name='password-confirm'),
    path('password/change/', ChangePasswordView.as_view(), name='password-change'),
    path('password/setup/', SetupPasswordView.as_view(), name='password-setup'),

    path('google/', GoogleLoginView.as_view(), name='google-login'),
]
