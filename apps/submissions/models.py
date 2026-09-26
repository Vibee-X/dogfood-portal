from django.db import models
from django.conf import settings


class Submission(models.Model):
    """A project submission for a hackathon event."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"

    team = models.ForeignKey(
        "teams.Team",
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    track = models.ForeignKey(
        "events.Track",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    fixture_id = models.CharField(max_length=64, null=True, blank=True)
    title = models.CharField(max_length=255)
    tagline = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    summary = models.TextField(blank=True, default="")
    thumbnail = models.CharField(max_length=500, blank=True, default="")
    gallery_images = models.JSONField(default=list, blank=True)
    demo_video_url = models.URLField(blank=True, default="")
    repo_url = models.URLField(blank=True, default="")
    live_url = models.URLField(blank=True, default="")
    tech_tags = models.JSONField(default=list, blank=True)
    custom_answers = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "fixture_id"],
                name="unique_submission_fixture_id_per_event",
            ),
        ]

    def __str__(self):
        return self.title
