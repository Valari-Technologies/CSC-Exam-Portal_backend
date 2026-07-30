from django.contrib import admin

from .models import SupportRequest


@admin.register(SupportRequest)
class SupportRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'school', 'issue_type', 'status', 'raised_by', 'created_at')
    list_filter = ('status', 'issue_type')
    search_fields = ('school__name', 'school__code', 'description')
    readonly_fields = ('created_at', 'updated_at')
