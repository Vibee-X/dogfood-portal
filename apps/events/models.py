from django.db import models
from django.conf import settings
from django.utils.text import slugify


class Event(models.Model):
    """A hackathon event with configurable dates."""
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True, default="")
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    submission_deadline = models.DateTimeField(null=True, blank=True)
    judging_start = models.DateTimeField(null=True, blank=True)
    judging_end = models.DateTimeField(null=True, blank=True)
    voting_start = models.DateTimeField(null=True, blank=True)
    voting_end = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_events",
    )
    reviews_per_submission = models.PositiveIntegerField(default=3)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Track(models.Model):
    """A category/track within an event."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tracks")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    class Meta:
        unique_together = [("event", "name")]

    def __str__(self):
        return f"{self.name} ({self.event})"


class Prize(models.Model):
    """A prize for an event, optionally tied to a track."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="prizes")
    track = models.ForeignKey(
        Track,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prizes",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    rank = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["rank"]

    def __str__(self):
        return f"{self.name} ({self.event})"
