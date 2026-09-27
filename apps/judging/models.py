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


class PairwiseComparison(models.Model):
    """One assigned judge's binary decision between two canonical submissions."""

    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="pairwise_comparisons",
    )
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pairwise_comparisons",
    )
    submission_a = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="pairwise_as_a",
    )
    submission_b = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="pairwise_as_b",
    )
    winner = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pairwise_wins",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["judge", "submission_a", "submission_b"],
                name="unique_pairwise_comparison_per_judge_pair",
            ),
            # Canonical ordering makes (A, B) and (B, A) the same stored pair,
            # while also preventing comparisons of a submission with itself.
            models.CheckConstraint(
                condition=models.Q(submission_a__lt=models.F("submission_b")),
                name="pairwise_submissions_are_canonical_and_distinct",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(winner__isnull=True)
                    | models.Q(winner=models.F("submission_a"))
                    | models.Q(winner=models.F("submission_b"))
                ),
                name="pairwise_winner_belongs_to_pair",
            ),
        ]
        ordering = ["submission_a_id", "submission_b_id", "pk"]

    def _canonicalize_pair(self):
        if self.submission_a_id and self.submission_b_id and self.submission_a_id > self.submission_b_id:
            submission_a = self.submission_a
            self.submission_a = self.submission_b
            self.submission_b = submission_a

    def clean(self):
        self._canonicalize_pair()
        super().clean()
        errors = {}
        if not self.event_id:
            errors["event"] = "An event is required."
        if not self.judge_id:
            errors["judge"] = "A judge is required."
        if not self.submission_a_id or not self.submission_b_id:
            errors["submission_a"] = "Two submissions are required."
        elif self.submission_a_id == self.submission_b_id:
            errors["submission_b"] = "A pair must contain two different submissions."
        else:
            if self.submission_a.event_id != self.event_id:
                errors["submission_a"] = "Submission A must belong to the comparison event."
            elif self.submission_a.status != "submitted":
                errors["submission_a"] = "Submission A must be submitted."
            if self.submission_b.event_id != self.event_id:
                errors["submission_b"] = "Submission B must belong to the comparison event."
            elif self.submission_b.status != "submitted":
                errors["submission_b"] = "Submission B must be submitted."

        if self.winner_id and self.winner_id not in {self.submission_a_id, self.submission_b_id}:
            errors["winner"] = "The winner must be submission A or submission B."

        if self.event_id and self.judge_id:
            from apps.accounts.models import EventMembership

            authorized_judge = EventMembership.objects.filter(
                user_id=self.judge_id,
                event_id=self.event_id,
                role=EventMembership.Role.JUDGE,
                status=EventMembership.Status.ACTIVE,
            ).exists()
            if not authorized_judge:
                errors["judge"] = "The comparison judge must be an active event judge."

        if self.event_id and self.judge_id and self.submission_a_id and self.submission_b_id:
            assigned_ids = set(
                JudgeAssignment.objects.filter(
                    event_id=self.event_id,
                    judge_id=self.judge_id,
                    submission_id__in=[self.submission_a_id, self.submission_b_id],
                ).values_list("submission_id", flat=True)
            )
            if self.submission_a_id not in assigned_ids:
                errors["submission_a"] = "Submission A is not assigned to this judge."
            if self.submission_b_id not in assigned_ids:
                errors["submission_b"] = "Submission B is not assigned to this judge."
            from apps.teams.models import TeamMembership

            scoped_track_ids = set(
                JudgeTrack.objects.filter(event_id=self.event_id, judge_id=self.judge_id)
                .values_list("track_id", flat=True)
            )
            for label, submission in (("submission_a", self.submission_a), ("submission_b", self.submission_b)):
                if TeamMembership.objects.filter(team=submission.team, user_id=self.judge_id).exists():
                    errors[label] = "A judge cannot compare their own team's submission."
                elif scoped_track_ids and submission.track_id not in scoped_track_ids:
                    errors[label] = "Submission is outside this judge's permitted track scope."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Normal saves enforce the event, authorization, and winner relationship;
        # the database constraints remain the final duplicate/canonical guard.
        self._canonicalize_pair()
        self.full_clean(validate_constraints=False)
        super().save(*args, **kwargs)

    def __str__(self):
        winner = self.winner_id or "pending"
        return f"{self.judge}: {self.submission_a_id} vs {self.submission_b_id} ({winner})"
