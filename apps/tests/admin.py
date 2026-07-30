from django.contrib import admin

from .models import Test, TestAssignment, TestAssignmentStudent, TestQuestion


class TestQuestionInline(admin.TabularInline):
    model = TestQuestion
    extra = 0
    raw_id_fields = ('question',)


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'subject', 'school_class', 'status', 'duration_minutes', 'total_marks', 'school', 'created_at',
    )
    list_filter = ('status', 'school', 'subject', 'school_class')
    search_fields = ('title',)
    inlines = (TestQuestionInline,)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(TestAssignment)
class TestAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        'test', 'assigned_to_type', 'school_class', 'section', 'start_datetime', 'end_datetime', 'is_active',
    )
    list_filter = ('assigned_to_type', 'is_active')


@admin.register(TestAssignmentStudent)
class TestAssignmentStudentAdmin(admin.ModelAdmin):
    list_display = ('assignment', 'student', 'is_notified', 'notified_at')
    list_filter = ('is_notified',)
