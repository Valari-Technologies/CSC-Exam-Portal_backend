from django.contrib import admin

from .models import School


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'city', 'state', 'status', 'created_at')
    list_filter = ('status', 'state')
    search_fields = ('name', 'code', 'city', 'official_email')
    readonly_fields = ('created_at', 'updated_at')
