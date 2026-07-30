from django.contrib import admin

from .models import BulkImportLog, StudentProfile


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'roll_number', 'school_class', 'section', 'school', 'is_active')
    list_filter = ('school', 'school_class', 'section', 'is_active')
    search_fields = ('user__email', 'user__full_name', 'roll_number', 'admission_number')


@admin.register(BulkImportLog)
class BulkImportLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'school', 'imported_by', 'file_name', 'status', 'success_count', 'fail_count', 'created_at')
    list_filter = ('status', 'school')
    readonly_fields = ('created_at', 'errors')
