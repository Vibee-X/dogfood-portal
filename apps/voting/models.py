from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError


class Vote(models.Model):
    """Community vote on a submission."""
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="votes",
    )
    voter_ref = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "voter_ref"],
                name="unique_vote_per_submission_voter_ref",
            ),
        ]

    def clean(self):
        super().clean()
        if self.submission_id and (
            self.submission.status != "submitted" or not self.submission.event.is_published
        ):
            raise ValidationError({"submission": "Only submitted public projects can receive votes."})

    def __str__(self):
        return f"Vote on {self.submission} by {self.voter_ref}"


class Comment(models.Model):
    """Comment on a submission."""
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="comments",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if not isinstance(self.body, str) or not self.body.strip():
            raise ValidationError({"body": "Comment body cannot be blank."})
        if len(self.body) > 2000:
            raise ValidationError({"body": "Comment body must be 2000 characters or fewer."})

    def __str__(self):
        return f"Comment by {self.user} on {self.submission}"
