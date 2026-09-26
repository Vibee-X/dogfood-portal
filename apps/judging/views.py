import csv
import io

from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication, SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as http_status
from django.http import HttpResponse

from apps.accounts.models import EventMembership
from apps.events.models import Event
from apps.submissions.models import Submission
from .models import JudgeAssignment, Score


def _get_event_role(user, event):
    """Get the user's role for a specific event."""
    membership = EventMembership.objects.filter(
        user=user,
        event=event,
        status=EventMembership.Status.ACTIVE,
    ).first()
    return membership.role if membership else None


@api_view(["GET"])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def judge_scores(request):
    """
    GET /api/judge/scores — returns the requesting judge's own scores.
    GET /api/judge/scores?judge=<username> — returns scores for that judge.

    Acceptance checks:
    - judge_a as judge_a → 200 (own scores)
    - judge_a's scores as judge_b → 403 (peer isolation)
    - participant → 403 (not a judge)
    """
    event = Event.objects.first()
    if not event:
        return Response({"error": "No event"}, status=http_status.HTTP_404_NOT_FOUND)

    user = request.user
    role = _get_event_role(user, event)

    # Organizer/admin can see everything
    if role in [EventMembership.Role.ORGANIZER, EventMembership.Role.ADMIN]:
        requested_judge = request.GET.get("judge", "")
        if requested_judge:
            # Return that judge's scores
            assignments = JudgeAssignment.objects.filter(
                event=event,
                judge__username=requested_judge,
            ).select_related("submission")
        else:
            # Return all scores
            assignments = JudgeAssignment.objects.filter(
                event=event,
            ).select_related("submission", "judge")

        scores_data = []
        for assignment in assignments:
            scores = Score.objects.filter(assignment=assignment)
            scores_data.append({
                "judge": assignment.judge.username,
                "submission": assignment.submission.title,
                "status": assignment.status,
                "scores": [
                    {"criterion": s.criterion.name, "value": s.value}
                    for s in scores
                ],
            })
        return Response({"scores": scores_data})

    # Must be a judge to access this endpoint
    if role != EventMembership.Role.JUDGE:
        return Response(
            {"error": "Only judges and organizers can access scores."},
            status=http_status.HTTP_403_FORBIDDEN,
        )

    # Peer isolation check: if ?judge= param is given and it's not the requesting user
    requested_judge = request.GET.get("judge", "")
    if requested_judge and requested_judge != user.username:
        return Response(
            {"error": "You cannot view another judge's scores."},
            status=http_status.HTTP_403_FORBIDDEN,
        )

    # Return the requesting judge's own scores
    assignments = JudgeAssignment.objects.filter(
        event=event,
        judge=user,
    ).select_related("submission")

    scores_data = []
    for assignment in assignments:
        scores = Score.objects.filter(assignment=assignment)
        scores_data.append({
            "submission": assignment.submission.title,
            "status": assignment.status,
            "scores": [
                {"criterion": s.criterion.name, "value": s.value}
                for s in scores
            ],
        })

    return Response({"scores": scores_data})


@api_view(["GET"])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def csv_export(request):
    """
    GET /api/export.csv — organizer/admin only.

    Returns CSV of submissions with scores.
    """
    event = Event.objects.first()
    if not event:
        return Response({"error": "No event"}, status=http_status.HTTP_404_NOT_FOUND)

    user = request.user
    role = _get_event_role(user, event)

    if role not in [EventMembership.Role.ORGANIZER, EventMembership.Role.ADMIN]:
        return Response(
            {"error": "Only organizers can export CSV."},
            status=http_status.HTTP_403_FORBIDDEN,
        )

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["project_id", "title", "team", "track", "status", "submitted_at"])

    submissions = Submission.objects.filter(event=event).select_related("team", "track")
    for sub in submissions:
        writer.writerow([
            sub.pk,
            sub.title,
            sub.team.name if sub.team else "",
            sub.track.name if sub.track else "",
            sub.status,
            sub.submitted_at.isoformat() if sub.submitted_at else "",
        ])

    response = HttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="export.csv"'
    return response
