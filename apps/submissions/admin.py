from django.contrib import admin
from .models import Submission

@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("title", "team", "event", "track", "status", "submitted_at")
    list_filter = ("status", "event", "track")
