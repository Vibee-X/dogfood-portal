"""Token-authenticated judging APIs and the organizer progress dashboard."""
import csv
import io
import json
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_http_methods
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from apps.accounts.models import EventMembership, User
from apps.events.models import Event, Track
from .models import (
    JudgeAssignment,
    JudgeTrack,
    NormalizationRun,
    PairwiseComparison,
    Rubric,
    RubricCriterion,
)
from .services import (
    AssignmentError,
    PairwisePermissionError,
    PairwiseStateError,
    PairwiseValidationError,
    ScoreWriteError,
    bradley_terry_ranking,
    event_role,
    generate_assignments,
    generate_pairwise_comparisons,
    is_organizer,
    judge_progress,
    next_pairwise_comparison,
    record_pairwise_winner,
    run_normalization,
    save_judge_score,
)


AUTHENTICATION = [TokenAuthentication, SessionAuthentication]


def _event_from_request(request, *, data=False):
    """Resolve an event by optional slug; legacy acceptance defaults to first."""
    source = request.data if data else request.query_params
    event_slug = source.get("event")
    if event_slug:
        return Event.objects.filter(slug=event_slug).first()
    return Event.objects.order_by("pk").first()


def _organizer_response(user, event):
    if not event:
        return Response({"error": "Event not found."}, status=http_status.HTTP_404_NOT_FOUND)
    if not is_organizer(user, event):
        return Response({"error": "Organizer access is required."}, status=http_status.HTTP_403_FORBIDDEN)
    return None


def _serialize_assignment(assignment, *, include_judge=False):
    payload = {
        "assignment_id": assignment.pk,
        "submission": {
            "id": assignment.submission_id,
            "fixture_id": assignment.submission.fixture_id,
            "title": assignment.submission.title,
            "track_id": assignment.submission.track_id,
            "track": assignment.submission.track.name if assignment.submission.track else None,
        },
        "status": assignment.status,
        "scores": [
            {
                "id": score.pk,
                "criterion_id": score.criterion_id,
                "criterion": score.criterion.name,
                "value": score.value,
                "max_score": score.criterion.max_score,
            }
            for score in assignment.scores.all().order_by("criterion_id")
        ],
    }
    if include_judge:
        payload["judge"] = assignment.judge.username
    return payload


def _serialize_pairwise_comparison(comparison, *, include_judge=False):
    def serialize_submission(submission):
        return {
            "id": submission.pk,
            "fixture_id": submission.fixture_id,
            "title": submission.title,
            "summary": submission.summary,
            "description": submission.description,
            "track": submission.track.name if submission.track else None,
            "track_id": submission.track_id,
            "repo_url": submission.repo_url,
            "live_url": submission.live_url,
            "demo_video_url": submission.demo_video_url,
            "tech_tags": submission.tech_tags,
        }

    payload = {
        "id": comparison.pk,
        "event": comparison.event.slug,
        "submission_a": serialize_submission(comparison.submission_a),
        "submission_b": serialize_submission(comparison.submission_b),
        "winner_id": comparison.winner_id,
        "completed": comparison.winner_id is not None,
        "created_at": comparison.created_at,
    }
    if include_judge:
        payload["judge"] = comparison.judge.username
    return payload


@extend_schema(
    methods=["GET"],
    tags=["Judging"],
    summary="Read authorized scores",
    description=(
        "Requires TokenAuthentication. Judges receive only their own assignments; "
        "organizers/admins may read scores in their event. Participants and peer "
        "judge reads are denied server-side."
    ),
    parameters=[
        OpenApiParameter("event", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("judge", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("assignment", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("track", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
    ],
    responses={200: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@extend_schema(
    methods=["POST", "PUT", "PATCH"],
    tags=["Judging"],
    summary="Create or update an assigned score",
    description=(
        "Requires TokenAuthentication and ownership of the assignment. The server "
        "validates judge, track, rubric, criterion, and event relationships and "
        "writes an audit record."
    ),
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["GET", "POST", "PUT", "PATCH"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def judge_scores(request):
    """Read a judge's own work or create/update one owned score."""
    if request.method == "GET":
        event = _event_from_request(request)
        if not event:
            return Response({"error": "Event not found."}, status=http_status.HTTP_404_NOT_FOUND)
        role = event_role(request.user, event)
        requested_judge = request.query_params.get("judge")
        assignments = JudgeAssignment.objects.filter(event=event).select_related(
            "judge", "submission", "submission__track"
        ).prefetch_related("scores__criterion")

        if role in {EventMembership.Role.ORGANIZER, EventMembership.Role.ADMIN}:
            if requested_judge:
                assignments = assignments.filter(judge__username=requested_judge)
            include_judge = True
        elif role == EventMembership.Role.JUDGE:
            if requested_judge and requested_judge != request.user.username:
                return Response(
                    {"error": "You cannot view another judge's scores."},
                    status=http_status.HTTP_403_FORBIDDEN,
                )
            assignments = assignments.filter(judge=request.user)
            include_judge = False
        else:
            return Response(
                {"error": "Only judges and organizers can access scores."},
                status=http_status.HTTP_403_FORBIDDEN,
            )

        # Optional filters narrow an already authorized queryset; they cannot
        # select peer data or another event.
        if request.query_params.get("assignment"):
            assignments = assignments.filter(pk=request.query_params["assignment"])
        if request.query_params.get("track"):
            assignments = assignments.filter(submission__track_id=request.query_params["track"])
        return Response({
            "event": event.slug,
            "scores": [
                _serialize_assignment(assignment, include_judge=include_judge)
                for assignment in assignments.order_by("pk")
            ],
        })

    try:
        score, created = save_judge_score(
            request.user,
            request.data.get("assignment_id"),
            request.data.get("criterion_id"),
            request.data.get("value"),
        )
    except ScoreWriteError as exc:
        return Response(exc.payload, status=exc.status)
    return Response(
        {"id": score.pk, "assignment_id": score.assignment_id, "criterion_id": score.criterion_id, "value": score.value},
        status=http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK,
    )


@extend_schema(
    methods=["GET"],
    tags=["Judging"],
    summary="List event rubrics",
    description="Organizer/admin only for the selected event.",
    parameters=[OpenApiParameter("event", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False)],
    responses={200: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@extend_schema(
    methods=["POST"],
    tags=["Judging"],
    summary="Create an event rubric",
    description="Organizer/admin only for the selected event.",
    request=OpenApiTypes.OBJECT,
    responses={201: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["GET", "POST"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def rubrics(request):
    event = _event_from_request(request, data=request.method == "POST")
    denied = _organizer_response(request.user, event)
    if denied:
        return denied
    if request.method == "GET":
        return Response({
            "event": event.slug,
            "rubrics": [
                {
                    "id": rubric.pk,
                    "name": rubric.name,
                    "is_active": rubric.is_active,
                    "criteria": [
                        {
                            "id": criterion.pk,
                            "name": criterion.name,
                            "description": criterion.description,
                            "weight": criterion.weight,
                            "max_score": criterion.max_score,
                        }
                        for criterion in rubric.criteria.order_by("pk")
                    ],
                }
                for rubric in Rubric.objects.filter(event=event).prefetch_related("criteria").order_by("pk")
            ],
        })
    rubric = Rubric(event=event, name=request.data.get("name", ""), is_active=bool(request.data.get("is_active", True)))
    try:
        rubric.full_clean()
    except Exception as exc:
        return Response(getattr(exc, "message_dict", {"error": str(exc)}), status=http_status.HTTP_400_BAD_REQUEST)
    rubric.save()
    return Response({"id": rubric.pk, "name": rubric.name}, status=http_status.HTTP_201_CREATED)


@extend_schema(
    methods=["POST"],
    tags=["Judging"],
    summary="Add a rubric criterion",
    description="Organizer/admin only for the rubric's event. Weight and maximum score must be positive.",
    request=OpenApiTypes.OBJECT,
    responses={201: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@extend_schema(
    methods=["PATCH"],
    tags=["Judging"],
    summary="Update a rubric criterion",
    description="Organizer/admin only for the rubric's event; `criterion_id` must belong to this rubric.",
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["POST", "PATCH"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def rubric_criteria(request, rubric_id):
    rubric = get_object_or_404(Rubric.objects.select_related("event"), pk=rubric_id)
    denied = _organizer_response(request.user, rubric.event)
    if denied:
        return denied
    criterion_id = request.data.get("criterion_id")
    criterion = RubricCriterion.objects.filter(pk=criterion_id, rubric=rubric).first() if criterion_id else None
    if request.method == "PATCH" and not criterion:
        return Response({"error": "criterion_id from this rubric is required."}, status=http_status.HTTP_404_NOT_FOUND)
    criterion = criterion or RubricCriterion(rubric=rubric)
    for field in ("name", "description", "weight", "max_score"):
        if field in request.data:
            setattr(criterion, field, request.data[field])
    try:
        criterion.full_clean()
    except Exception as exc:
        return Response(getattr(exc, "message_dict", {"error": str(exc)}), status=http_status.HTTP_400_BAD_REQUEST)
    criterion.save()
    return Response(
        {"id": criterion.pk, "name": criterion.name, "weight": criterion.weight, "max_score": criterion.max_score},
        status=http_status.HTTP_201_CREATED if request.method == "POST" else http_status.HTTP_200_OK,
    )


@extend_schema(
    tags=["Judging"],
    summary="Invite or scope a judge",
    description="Organizer/admin only. Replaces this judge's event track scopes with the supplied event track IDs.",
    request=OpenApiTypes.OBJECT,
    responses={201: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["POST"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def invite_judge(request):
    event = _event_from_request(request, data=True)
    denied = _organizer_response(request.user, event)
    if denied:
        return denied
    judge = User.objects.filter(username=request.data.get("username", "")).first()
    if not judge:
        return Response({"error": "User not found."}, status=http_status.HTTP_404_NOT_FOUND)
    membership = EventMembership.objects.filter(user=judge, event=event).first()
    if membership and membership.role in {EventMembership.Role.ORGANIZER, EventMembership.Role.ADMIN}:
        return Response({"error": "An organizer or admin cannot be downgraded to judge."}, status=http_status.HTTP_400_BAD_REQUEST)
    EventMembership.objects.update_or_create(
        user=judge,
        event=event,
        defaults={
            "role": EventMembership.Role.JUDGE,
            "status": EventMembership.Status.ACTIVE,
            "invited_by": request.user,
        },
    )
    track_ids = request.data.get("track_ids", [])
    if not isinstance(track_ids, list):
        return Response({"error": "track_ids must be a list."}, status=http_status.HTTP_400_BAD_REQUEST)
    tracks = list(Track.objects.filter(event=event, pk__in=track_ids).order_by("pk"))
    if len({track.pk for track in tracks}) != len(set(track_ids)):
        return Response({"error": "Every track_id must belong to the event."}, status=http_status.HTTP_400_BAD_REQUEST)
    JudgeTrack.objects.filter(event=event, judge=judge).delete()
    JudgeTrack.objects.bulk_create([
        JudgeTrack(event=event, judge=judge, track=track) for track in tracks
    ])
    return Response({
        "username": judge.username,
        "role": EventMembership.Role.JUDGE,
        "track_ids": [track.pk for track in tracks],
    }, status=http_status.HTTP_201_CREATED)


@extend_schema(
    tags=["Judging"],
    summary="Generate judge assignments",
    description="Organizer/admin only. Produces deterministic, idempotent, track-scoped assignments for submitted projects.",
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["POST"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def generate_judge_assignments(request):
    event = _event_from_request(request, data=True)
    denied = _organizer_response(request.user, event)
    if denied:
        return denied
    try:
        result = generate_assignments(event)
    except AssignmentError as exc:
        return Response({"error": str(exc)}, status=http_status.HTTP_400_BAD_REQUEST)
    return Response({"event": event.slug, **result})


@extend_schema(
    tags=["Judging"],
    summary="Read judge progress",
    description="Organizer/admin only for the selected event.",
    parameters=[OpenApiParameter("event", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False)],
    responses={200: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def judge_progress_api(request):
    event = _event_from_request(request)
    denied = _organizer_response(request.user, event)
    if denied:
        return denied
    return Response({"event": event.slug, "judges": judge_progress(event)})


@extend_schema(
    tags=["Judging"],
    summary="Persist a normalization snapshot",
    description="Organizer/admin only. Creates a weighted z-score NormalizationRun snapshot for the selected event.",
    request=OpenApiTypes.OBJECT,
    responses={201: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["POST"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def normalize_scores(request):
    event = _event_from_request(request, data=True)
    denied = _organizer_response(request.user, event)
    if denied:
        return denied
    run = run_normalization(event)
    return Response({"id": run.pk, "event": event.slug, "method": run.method}, status=http_status.HTTP_201_CREATED)


@extend_schema(
    tags=["Pairwise"],
    summary="Generate deterministic judge comparison pairs",
    description=(
        "Organizer/admin only. Creates every missing canonical pair from each "
        "judge's currently assigned, in-scope submitted projects; repeat calls "
        "preserve completed decisions and create no duplicates."
    ),
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["POST"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def generate_pairwise(request):
    event = _event_from_request(request, data=True)
    denied = _organizer_response(request.user, event)
    if denied:
        return denied
    try:
        result = generate_pairwise_comparisons(event, actor=request.user)
    except ValidationError as exc:
        return Response(exc.message_dict, status=http_status.HTTP_400_BAD_REQUEST)
    return Response({"event": event.slug, **result})


@extend_schema(
    tags=["Pairwise"],
    summary="Get the next assigned pairwise comparison",
    description=(
        "Active judges only. The result is the caller's first pending, currently "
        "authorized comparison in deterministic canonical-pair order."
    ),
    parameters=[OpenApiParameter("event", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False)],
    responses={200: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def next_pairwise(request):
    event = _event_from_request(request)
    if not event:
        return Response({"error": "Event not found."}, status=http_status.HTTP_404_NOT_FOUND)
    try:
        comparison = next_pairwise_comparison(event, request.user)
    except PairwisePermissionError as exc:
        return Response({"error": str(exc)}, status=http_status.HTTP_403_FORBIDDEN)
    if not comparison:
        return Response({"error": "No pending pairwise comparison is assigned."}, status=http_status.HTTP_404_NOT_FOUND)
    return Response(_serialize_pairwise_comparison(comparison))


@extend_schema(
    methods=["GET"],
    tags=["Pairwise"],
    summary="List authorized pairwise comparisons",
    description=(
        "Judges can list only their own comparisons. Organizer/admin callers may "
        "inspect comparisons in their event and optionally narrow by `judge`; a "
        "judge cannot use that parameter to inspect a peer. Set `completed=1` to "
        "return only completed comparisons."
    ),
    parameters=[
        OpenApiParameter("event", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("judge", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("completed", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False),
    ],
    responses={200: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@extend_schema(
    methods=["POST"],
    tags=["Pairwise"],
    summary="Submit a pairwise winner",
    description=(
        "Active judges only. A caller may complete exactly one of their own pending "
        "assigned pairs; `winner_id` must be submission A or B. The write is audit logged."
    ),
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT},
)
@api_view(["GET", "POST"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def pairwise_comparisons(request):
    if request.method == "GET":
        event = _event_from_request(request)
        if not event:
            return Response({"error": "Event not found."}, status=http_status.HTTP_404_NOT_FOUND)
        role = event_role(request.user, event)
        requested_judge = request.query_params.get("judge")
        queryset = PairwiseComparison.objects.filter(event=event).select_related(
            "event", "judge", "submission_a__track", "submission_b__track"
        )
        if role in {EventMembership.Role.ORGANIZER, EventMembership.Role.ADMIN}:
            if requested_judge:
                queryset = queryset.filter(judge__username=requested_judge)
            include_judge = True
        elif role == EventMembership.Role.JUDGE:
            if requested_judge and requested_judge != request.user.username:
                return Response(
                    {"error": "You cannot view another judge's comparisons."},
                    status=http_status.HTTP_403_FORBIDDEN,
                )
            queryset = queryset.filter(judge=request.user)
            include_judge = False
        else:
            return Response(
                {"error": "Only judges and organizers can access pairwise comparisons."},
                status=http_status.HTTP_403_FORBIDDEN,
            )
        if request.query_params.get("completed") in {"1", "true", "True"}:
            queryset = queryset.filter(winner__isnull=False)
        return Response({
            "event": event.slug,
            "comparisons": [
                _serialize_pairwise_comparison(comparison, include_judge=include_judge)
                for comparison in queryset.order_by("submission_a_id", "submission_b_id", "pk")
            ],
        })

    event = _event_from_request(request, data=True)
    if not event:
        return Response({"error": "Event not found."}, status=http_status.HTTP_404_NOT_FOUND)
    comparison_id = request.data.get("comparison_id")
    winner_id = request.data.get("winner_id")
    if not comparison_id or not winner_id:
        return Response(
            {"error": "comparison_id and winner_id are required."},
            status=http_status.HTTP_400_BAD_REQUEST,
        )
    try:
        winner_id = int(winner_id)
    except (TypeError, ValueError):
        return Response({"error": "comparison_id and winner_id must be integers."}, status=http_status.HTTP_400_BAD_REQUEST)
    try:
        comparison = record_pairwise_winner(
            event,
            request.user,
            comparison_id,
            winner_id,
            actor=request.user,
        )
    except PairwisePermissionError as exc:
        return Response({"error": str(exc)}, status=http_status.HTTP_403_FORBIDDEN)
    except PairwiseStateError as exc:
        return Response({"error": str(exc)}, status=http_status.HTTP_409_CONFLICT)
    except PairwiseValidationError as exc:
        return Response({"error": str(exc)}, status=http_status.HTTP_400_BAD_REQUEST)
    return Response(_serialize_pairwise_comparison(comparison))


@extend_schema(
    tags=["Pairwise"],
    summary="Read Bradley-Terry pairwise ranking",
    description=(
        "Organizer/admin only. Estimates strengths separately for each connected "
        "comparison component; unconnected components and submissions with no "
        "comparisons are explicitly not globally ranked."
    ),
    parameters=[OpenApiParameter("event", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False)],
    responses={200: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def pairwise_rankings(request):
    event = _event_from_request(request)
    denied = _organizer_response(request.user, event)
    if denied:
        return denied
    try:
        return Response(bradley_terry_ranking(event))
    except RuntimeError as exc:
        return Response({"error": str(exc)}, status=http_status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Judging"],
    summary="Export event judging data as CSV",
    description="Organizer/admin only for the selected event. Includes submissions, raw scores, normalization values, and judge progress.",
    parameters=[OpenApiParameter("event", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False)],
    responses={200: OpenApiTypes.STR, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
@authentication_classes(AUTHENTICATION)
@permission_classes([IsAuthenticated])
def csv_export(request):
    """Export submissions, raw/normalized scores, and judge progress."""
    event = _event_from_request(request)
    denied = _organizer_response(request.user, event)
    if denied:
        return denied
    normalization = NormalizationRun.objects.filter(event=event).order_by("-computed_at", "-pk").first()
    snapshot = normalization.snapshot if normalization else {}
    assignment_snapshot = {str(record["assignment_id"]): record for record in snapshot.get("assignments", [])}
    project_snapshot = snapshot.get("projects", {})
    progress_by_judge = {item["judge"]: item for item in judge_progress(event)}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "submission_id", "fixture_id", "title", "team", "track", "submission_status", "submitted_at",
        "judge", "assignment_status", "raw_weighted_score", "normalized_score",
        "project_normalized_score", "raw_criteria", "judge_assigned", "judge_completed",
        "judge_remaining", "judge_completion_percentage",
    ])
    submissions = event.submissions.select_related("team", "track").order_by("pk")
    assignments_by_submission = defaultdict(list)
    for assignment in JudgeAssignment.objects.filter(event=event).select_related("judge", "submission").order_by("pk"):
        assignments_by_submission[assignment.submission_id].append(assignment)
    for submission in submissions:
        for assignment in assignments_by_submission.get(submission.pk, [None]):
            record = assignment_snapshot.get(str(assignment.pk), {}) if assignment else {}
            project = project_snapshot.get(str(submission.pk), {})
            progress = progress_by_judge.get(assignment.judge.username, {}) if assignment else {}
            writer.writerow([
                submission.pk, submission.fixture_id or "", submission.title, submission.team.name,
                submission.track.name if submission.track else "", submission.status,
                submission.submitted_at.isoformat() if submission.submitted_at else "",
                assignment.judge.username if assignment else "", assignment.status if assignment else "",
                record.get("raw_weighted_score", ""), record.get("normalized_score", ""),
                project.get("normalized_mean", ""), json.dumps(record.get("criteria", []), sort_keys=True),
                progress.get("assigned", ""), progress.get("completed", ""), progress.get("remaining", ""),
                progress.get("completion_percentage", ""),
            ])
    response = HttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="dogfood-judging-export.csv"'
    return response


@login_required
@require_GET
def progress_dashboard(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not is_organizer(request.user, event):
        return HttpResponseForbidden("Organizer access is required.")
    context = {"event": event, "progress": judge_progress(event)}
    if request.headers.get("HX-Request") == "true":
        return render(request, "judging/progress_table.html", context)
    return render(request, "judging/progress.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def pairwise_dashboard(request, slug):
    """Server-rendered, HTMX-enhanced judge workflow for assigned pairs."""
    event = get_object_or_404(Event, slug=slug)
    if event_role(request.user, event) != EventMembership.Role.JUDGE:
        return HttpResponseForbidden("Active judge access is required.")

    if request.method == "POST":
        comparison_id = request.POST.get("comparison_id")
        winner_id = request.POST.get("winner_id")
        try:
            winner_id = int(winner_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("winner_id must be an integer.")
        try:
            record_pairwise_winner(event, request.user, comparison_id, winner_id, actor=request.user)
        except PairwisePermissionError as exc:
            return HttpResponseForbidden(str(exc))
        except PairwiseStateError as exc:
            return HttpResponseBadRequest(str(exc))
        except PairwiseValidationError as exc:
            return HttpResponseBadRequest(str(exc))
        if request.headers.get("HX-Request") != "true":
            return redirect("judging_web:pairwise_dashboard", slug=event.slug)

    comparison = next_pairwise_comparison(event, request.user)
    context = {"event": event, "comparison": comparison}
    if request.headers.get("HX-Request") == "true":
        return render(request, "judging/pairwise_content.html", context)
    return render(request, "judging/pairwise.html", context)
