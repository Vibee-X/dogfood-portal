from django.contrib import admin
from .models import Vote, Comment


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ("submission", "voter_ref", "created_at")
    search_fields = ("submission__title", "voter_ref")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("submission", "user", "is_hidden", "created_at")
    list_filter = ("is_hidden",)
    search_fields = ("submission__title", "user__username", "body")
