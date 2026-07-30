from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import RefreshToken, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ('email', 'full_name', 'role', 'school', 'is_active', 'is_verified', 'created_at')
    list_filter = ('role', 'is_active', 'is_verified', 'school')
    search_fields = ('email', 'full_name')
    ordering = ('-created_at',)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Profile', {'fields': ('full_name', 'role', 'school', 'profile_picture')}),
        ('OAuth', {'fields': ('oauth_provider', 'oauth_id')}),
        ('Status', {'fields': ('is_active', 'is_verified', 'is_staff', 'is_superuser')}),
        ('Permissions', {'fields': ('groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login',)}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'full_name', 'role', 'school', 'password1', 'password2'),
        }),
    )
    readonly_fields = ('last_login',)


@admin.register(RefreshToken)
class RefreshTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'expires_at', 'revoked', 'created_at')
    list_filter = ('revoked',)
    search_fields = ('user__email',)
    readonly_fields = ('token', 'created_at')
