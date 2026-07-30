"""Shared ViewSet mixins for multi-tenant school scoping."""


class SchoolScopedQuerysetMixin:
    """Restrict ViewSet queryset to the requesting user's school.

    CSC Admin sees everything (no filter applied). All other roles
    are filtered by `school_id`. Override `school_field` if the model
    accesses the school through a relationship (e.g. `school_field = 'subject__school'`).
    """

    school_field = 'school'

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user.is_authenticated:
            return qs.none()
        if user.role == 'csc_admin':
            return qs
        if user.school_id is None:
            return qs.none()
        return qs.filter(**{self.school_field: user.school_id})


class AutoSetSchoolMixin:
    """Auto-fill `school` on create from the requesting user's school."""

    school_field = 'school'

    def perform_create(self, serializer):
        user = self.request.user
        if user.role != 'csc_admin' and user.school_id:
            serializer.save(**{self.school_field: user.school})
        else:
            serializer.save()
