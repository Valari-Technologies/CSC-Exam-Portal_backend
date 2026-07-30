from django.contrib import admin

from .models import Result, ResultDetail


class ResultDetailInline(admin.TabularInline):
    model = ResultDetail
    extra = 0
    readonly_fields = ('question', 'selected_option', 'correct_option', 'is_correct', 'marks_obtained')
    can_delete = False


@admin.register(Result)
class ResultAdmin(admin.ModelAdmin):
    list_display = ('student', 'test', 'obtained_marks', 'total_marks', 'percentage', 'passed', 'rank', 'is_published')
    list_filter = ('is_published', 'passed', 'test')
    search_fields = ('student__email', 'test__title')
    readonly_fields = ('calculated_at',)
    inlines = (ResultDetailInline,)
