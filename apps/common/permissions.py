"""Custom DRF permission classes for role-based access control."""
from rest_framework.permissions import BasePermission


class IsCSCAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == 'csc_admin'
        )


class IsSchoolAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == 'school_admin'
        )


class IsTeacher(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == 'teacher'
        )


class IsStudent(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == 'student'
        )


class IsCSCOrSchoolAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in ('csc_admin', 'school_admin')
        )


class IsTeacherOrAbove(BasePermission):
    """Teacher, School Admin, or CSC Admin."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in ('csc_admin', 'school_admin', 'teacher')
        )


class IsSchoolMember(BasePermission):
    """Object permission: user belongs to the same school as the object.

    CSC Admin bypasses school check. Object must expose `school_id` or `school`.
    """

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.role == 'csc_admin':
            return True
        obj_school_id = getattr(obj, 'school_id', None)
        return obj_school_id is not None and obj_school_id == request.user.school_id
