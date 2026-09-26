from django.contrib import admin
from .models import Event, Track, Prize


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "submission_deadline", "is_published")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ("name", "event")


@admin.register(Prize)
class PrizeAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "track", "rank")
