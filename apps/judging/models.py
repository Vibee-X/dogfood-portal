from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings


class Rubric(models.Model):
    """Scoring rubric for an event."""
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="rubrics")
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "name"],
                name="unique_rubric_name_per_event",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.event})"


class RubricCriterion(models.Model):
    """Individual criterion within a rubric."""
    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="criteria")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    weight = models.FloatField(default=1.0)
    max_score = models.PositiveIntegerField(default=5)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rubric", "name"],
                name="unique_criterion_name_per_rubric",
            ),
            models.CheckConstraint(
                condition=models.Q(weight__gt=0),
                name="criterion_weight_is_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(max_score__gt=0),
                name="criterion_max_score_is_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.weight is None or self.weight <= 0:
            raise ValidationError({"weight": "Weight must be greater than zero."})
        if self.max_score is None or self.max_score <= 0:
            raise ValidationError({"max_score": "Maximum score must be greater than zero."})

    def __str__(self):
        return f"{self.name} (weight={self.weight})"


class JudgeTrack(models.Model):
    """The event tracks a judge may review; no rows means all event tracks."""

    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="judge_tracks",
    )
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="judge_track_scopes",
    )
    track = models.ForeignKey(
        "events.Track",
        on_delete=models.CASCADE,
        related_name="judge_scopes",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "judge", "track"],
                name="unique_judge_track_scope",
            ),
        ]

    def clean(self):
        super().clean()
        if self.track_id and self.event_id and self.track.event_id != self.event_id:
            raise ValidationError({"track": "Track must belong to the assignment event."})

    def __str__(self):
        return f"{self.judge} may review {self.track}"


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

    def clean(self):
        super().clean()
        if self.submission_id and self.event_id and self.submission.event_id != self.event_id:
            raise ValidationError({"submission": "Submission must belong to the assignment event."})
        if self.judge_id and self.submission_id:
            from apps.teams.models import TeamMembership

            if TeamMembership.objects.filter(team=self.submission.team, user_id=self.judge_id).exists():
                raise ValidationError({"judge": "A judge cannot review their own team's submission."})

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

    def clean(self):
        super().clean()
        if self.assignment_id and self.criterion_id:
            if self.assignment.event_id != self.criterion.rubric.event_id:
                raise ValidationError({"criterion": "Criterion must belong to the assignment event."})
            if self.value is None or not 1 <= self.value <= self.criterion.max_score:
                raise ValidationError({
                    "value": f"Value must be between 1 and {self.criterion.max_score}."
                })

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
