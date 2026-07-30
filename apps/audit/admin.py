from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user', 'action', 'status', 'entity_type', 'entity_id', 'ip_address', 'created_at',
    )
    list_filter = ('action', 'status')
    search_fields = ('user__email', 'entity_type', 'ip_address')
    readonly_fields = (
        'user', 'action', 'entity_type', 'entity_id', 'ip_address', 'user_agent',
        'status', 'details', 'created_at',
    )
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
