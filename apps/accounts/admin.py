from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, EventMembership


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "display_name", "is_staff")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Profile", {"fields": ("display_name",)}),
    )


@admin.register(EventMembership)
class EventMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "event", "role", "status", "created_at")
    list_filter = ("role", "status")
