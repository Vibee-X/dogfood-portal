from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings


class User(AbstractUser):
    """Custom user model with display_name."""
    display_name = models.CharField(max_length=255, blank=True, default="")

    def __str__(self):
        return self.display_name or self.username


class EventMembership(models.Model):
    """Per-event role assignment. Roles are NOT global on User."""

    class Role(models.TextChoices):
        PARTICIPANT = "participant", "Participant"
        JUDGE = "judge", "Judge"
        ORGANIZER = "organizer", "Organizer"
        ADMIN = "admin", "Admin"

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_memberships",
    )
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PARTICIPANT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations_sent",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "event")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.role} @ {self.event}"
