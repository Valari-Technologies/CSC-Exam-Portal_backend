from django.contrib import admin

from .models import AntiCheatLog, ExamAnswer, ExamSession


@admin.register(ExamSession)
class ExamSessionAdmin(admin.ModelAdmin):
    list_display = ('student', 'test', 'status', 'started_at', 'submitted_at', 'time_remaining_seconds')
    list_filter = ('status',)
    search_fields = ('student__email', 'test__title')
    readonly_fields = ('started_at',)


@admin.register(ExamAnswer)
class ExamAnswerAdmin(admin.ModelAdmin):
    list_display = ('session', 'question', 'selected_option', 'time_spent_seconds', 'last_updated_at')
    list_filter = ('selected_option',)


@admin.register(AntiCheatLog)
class AntiCheatLogAdmin(admin.ModelAdmin):
    list_display = ('session', 'event_type', 'event_count', 'occurred_at')
    list_filter = ('event_type',)
