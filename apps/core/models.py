from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    """Tracks important actions for accountability."""
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=255)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.actor} — {self.action} on {self.target_type}:{self.target_id}"


class Certificate(models.Model):
    """Participation/award certificates."""
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="certificates",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="certificates",
    )
    team = models.ForeignKey(
        "teams.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates",
    )
    type = models.CharField(max_length=50)
    issued_at = models.DateTimeField(auto_now_add=True)
    verification_code = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return f"{self.type} — {self.user} — {self.event}"
