"""Read-only helpers for showing AuditLog entries per event.

AuditLog has no event column, and the event slug some writers store in
``metadata`` can change when an organizer edits the event. Entries are
therefore scoped through the foreign keys of the row they point at
(``target_type`` + ``target_id``), never through slugs. An entry whose target
row no longer exists cannot be attributed safely and is left out.
"""
from django.db.models import CharField, Q
from django.db.models.functions import Cast

from apps.core.models import AuditLog, Certificate
from apps.judging.models import PairwiseComparison, Score
from apps.voting.models import Comment, Vote

# target_type values exactly as the writers store them, with a display label.
TARGET_LABELS = {
    "Score": "Score",
    "PairwiseComparison": "Pairwise comparison",
    "Vote": "Vote",
    "Comment": "Comment",
    "certificate": "Certificate",
}


def _target_keys(queryset):
    """Primary keys as strings, matching AuditLog.target_id."""
    return queryset.annotate(target_key=Cast("pk", output_field=CharField())).values("target_key")


def event_audit_entries(event):
    """All AuditLog entries whose target row belongs to ``event``, newest first."""
    in_event = (
        Q(target_type="Score", target_id__in=_target_keys(Score.objects.filter(assignment__event=event)))
        | Q(target_type="PairwiseComparison", target_id__in=_target_keys(PairwiseComparison.objects.filter(event=event)))
        | Q(target_type="Vote", target_id__in=_target_keys(Vote.objects.filter(submission__event=event)))
        | Q(target_type="Comment", target_id__in=_target_keys(Comment.objects.filter(submission__event=event)))
        | Q(target_type="certificate", target_id__in=_target_keys(Certificate.objects.filter(event=event)))
    )
    return AuditLog.objects.filter(in_event).select_related("actor").order_by("-created_at", "-pk")
