from django.contrib import admin

from .models import Chapter, Class, Section, Subject


@admin.register(Class)
class ClassAdmin(admin.ModelAdmin):
    list_display = ('name', 'school', 'numeric_value', 'is_active')
    list_filter = ('school', 'is_active')
    search_fields = ('name', 'school__name')


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'school_class', 'school', 'is_active')
    list_filter = ('school', 'school_class', 'is_active')


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'school_class', 'school', 'is_active')
    list_filter = ('school', 'school_class', 'is_active')
    search_fields = ('name', 'code')


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ('name', 'subject', 'order_number', 'is_active')
    list_filter = ('subject', 'is_active')
    search_fields = ('name', 'subject__name')
    ordering = ('subject', 'order_number')
