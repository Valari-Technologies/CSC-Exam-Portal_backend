from django.contrib import admin

from .models import Question


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('id', 'subject', 'chapter', 'difficulty', 'correct_option', 'marks', 'is_active', 'created_at')
    list_filter = ('difficulty', 'is_active', 'subject', 'school')
    search_fields = ('question_text',)
    readonly_fields = ('created_at', 'updated_at')
