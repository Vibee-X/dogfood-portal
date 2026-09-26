import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone


class Team(models.Model):
    """A team within an event."""
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="teams",
    )
    fixture_id = models.CharField(max_length=64, null=True, blank=True)
    name = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_teams",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "fixture_id"],
                name="unique_team_fixture_id_per_event",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.event})"


class TeamMembership(models.Model):
    """Membership linking users to teams."""
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="team_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("team", "user")]

    def __str__(self):
        return f"{self.user} in {self.team}"


class InviteLink(models.Model):
    """Shareable invite link for a team."""
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invite_links")
    code = models.CharField(max_length=64, unique=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(default=0)  # 0 = unlimited
    uses_count = models.PositiveIntegerField(default=0)

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = uuid.uuid4().hex[:12]
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        if self.max_uses > 0 and self.uses_count >= self.max_uses:
            return False
        return True

    def __str__(self):
        return f"Invite {self.code} for {self.team}"
