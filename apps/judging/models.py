from django.db import models
from django.conf import settings


class Rubric(models.Model):
    """Scoring rubric for an event."""
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="rubrics")
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.event})"


class RubricCriterion(models.Model):
    """Individual criterion within a rubric."""
    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="criteria")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    weight = models.FloatField(default=1.0)
    max_score = models.PositiveIntegerField(default=5)

    def __str__(self):
        return f"{self.name} (weight={self.weight})"


class JudgeAssignment(models.Model):
    """Assignment of a judge to review a submission."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="judge_assignments")
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="judge_assignments")
    submission = models.ForeignKey("submissions.Submission", on_delete=models.CASCADE, related_name="judge_assignments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    batch_id = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("judge", "submission")]

    def __str__(self):
        return f"{self.judge} → {self.submission}"


class Score(models.Model):
    """A score given by a judge for a specific criterion on an assignment."""
    assignment = models.ForeignKey(JudgeAssignment, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(RubricCriterion, on_delete=models.CASCADE, related_name="scores")
    value = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("assignment", "criterion")]

    def __str__(self):
        return f"{self.assignment} — {self.criterion}: {self.value}"


class NormalizationRun(models.Model):
    """Snapshot of a normalization computation."""
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="normalization_runs")
    method = models.CharField(max_length=100, default="zscore")
    computed_at = models.DateTimeField(auto_now_add=True)
    snapshot = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Normalization {self.method} @ {self.computed_at}"
