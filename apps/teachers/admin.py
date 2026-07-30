from django.contrib import admin

from .models import TeacherAssignment, TeacherProfile


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'school', 'employee_id', 'is_active', 'joining_date')
    list_filter = ('school', 'is_active')
    search_fields = ('user__email', 'user__full_name', 'employee_id')


@admin.register(TeacherAssignment)
class TeacherAssignmentAdmin(admin.ModelAdmin):
    list_display = ('teacher', 'subject', 'school_class', 'section', 'academic_year', 'assigned_at')
    list_filter = ('academic_year', 'school_class')
    search_fields = ('teacher__user__email', 'subject__name')
