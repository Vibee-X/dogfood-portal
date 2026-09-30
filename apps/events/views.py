from urllib.parse import urlencode

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from django.views.decorators.http import require_GET
from .models import Event, Track, Prize
from .forms import EventForm, TrackForm, PrizeForm
from apps.accounts.models import EventMembership
from apps.core.audit import TARGET_LABELS, event_audit_entries
from apps.core.models import Certificate
from apps.judging.models import JudgeAssignment, RubricCriterion
from apps.submissions.models import Submission
from apps.teams.models import Team

AUDIT_PAGE_SIZE = 25
TRACK_CHIP_LIMIT = 4
_SUBMISSION_KEYS = ("submission_id", "submission_a_id", "submission_b_id", "winner_id")


def _is_organizer(user, event):
    """Check if user is organizer or admin for this event."""
    if not user.is_authenticated:
        return False
    return EventMembership.objects.filter(
        user=user,
        event=event,
        role__in=[EventMembership.Role.ORGANIZER, EventMembership.Role.ADMIN],
        status=EventMembership.Status.ACTIVE,
    ).exists()


def _related_count(queryset):
    """Count of ``queryset`` rows whose ``event`` is the outer event, as a subquery."""
    counted = (
        queryset.filter(event=OuterRef("pk"))
        .order_by()
        .values("event")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return Coalesce(Subquery(counted, output_field=IntegerField()), 0)


def event_list(request):
    """List all published events, optionally filtered by ?status=."""
    # Each count is a correlated subquery, so the page stays at two queries
    # (events + one prefetch of track names) however many events there are,
    # and the counts never multiply through joins.
    events = (
        Event.objects.filter(is_published=True)
        .annotate(
            project_count=_related_count(Submission.objects.filter(status=Submission.Status.SUBMITTED)),
            track_count=_related_count(Track.objects.all()),
            prize_count=_related_count(Prize.objects.all()),
            team_count=_related_count(Team.objects.all()),
        )
        .prefetch_related(Prefetch("tracks", queryset=Track.objects.order_by("name")))
        .order_by("-created_at")
    )
    status_filter = request.GET.get("status", "").strip().lower()
    if status_filter not in Event.STATUS_LABELS:
        status_filter = ""
    if status_filter:
        # Status is derived in Python from the event's dates (Event.status),
        # so filter with the same rule the badges use.
        events = [event for event in events if event.status == status_filter]
    events = list(events)
    for event in events:
        names = [track.name for track in event.tracks.all()]  # prefetched, no query
        event.track_chips = names[:TRACK_CHIP_LIMIT]
        event.more_tracks = max(len(names) - TRACK_CHIP_LIMIT, 0)
    return render(request, "events/event_list.html", {
        "events": events,
        "status_filter": status_filter,
        "status_filter_label": Event.STATUS_LABELS.get(status_filter, ""),
    })


@login_required
def event_create(request):
    """Create a new event — any logged-in user becomes organizer."""
    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.created_by = request.user
            event.save()
            # Creator becomes organizer
            EventMembership.objects.create(
                user=request.user,
                event=event,
                role=EventMembership.Role.ORGANIZER,
            )
            messages.success(request, f"Event '{event.name}' created.")
            return redirect("events:event_detail", slug=event.slug)
    else:
        form = EventForm()
    return render(request, "events/event_form.html", {"form": form, "editing": False})


def event_detail(request, slug):
    """View event details."""
    event = get_object_or_404(Event, slug=slug)
    is_org = _is_organizer(request.user, event)
    is_judge = request.user.is_authenticated and EventMembership.objects.filter(
        user=request.user,
        event=event,
        role=EventMembership.Role.JUDGE,
        status=EventMembership.Status.ACTIVE,
    ).exists()
    tracks = event.tracks.all()
    prizes = event.prizes.all()
    participation_certificate = None
    can_request_certificate = False
    if request.user.is_authenticated:
        can_request_certificate = EventMembership.objects.filter(
            user=request.user,
            event=event,
            status=EventMembership.Status.ACTIVE,
        ).exists()
        if can_request_certificate:
            participation_certificate = Certificate.objects.filter(
                event=event,
                user=request.user,
                type="participation",
            ).order_by("pk").first()
    return render(request, "events/event_detail.html", {
        "event": event,
        "is_organizer": is_org,
        "is_judge": is_judge,
        "tracks": tracks,
        "prizes": prizes,
        "can_request_certificate": can_request_certificate,
        "participation_certificate": participation_certificate,
    })


def _audit_badge(action):
    if action.endswith(".hidden"):
        return "badge-danger"
    if action.endswith((".created", ".completed", ".issued", ".assigned")):
        return "badge-success"
    if action.endswith((".updated", ".unhidden")):
        return "badge-info"
    return ""


def _describe_audit_entries(entries, event):
    """Attach display fields; ids in metadata resolve to names in this event only."""
    submission_ids, assignment_ids, criterion_ids, user_ids = set(), set(), set(), set()
    for entry in entries:
        metadata = entry.metadata or {}
        submission_ids.update(metadata[key] for key in _SUBMISSION_KEYS if isinstance(metadata.get(key), int))
        if isinstance(metadata.get("assignment_id"), int):
            assignment_ids.add(metadata["assignment_id"])
        if isinstance(metadata.get("criterion_id"), int):
            criterion_ids.add(metadata["criterion_id"])
        if isinstance(metadata.get("judge_id"), int):
            user_ids.add(metadata["judge_id"])
    titles = dict(Submission.objects.filter(event=event, pk__in=submission_ids).values_list("pk", "title"))
    assignments = {
        a.pk: a for a in JudgeAssignment.objects.filter(event=event, pk__in=assignment_ids).select_related("submission", "judge")
    }
    criteria = dict(RubricCriterion.objects.filter(rubric__event=event, pk__in=criterion_ids).values_list("pk", "name"))
    usernames = dict(
        get_user_model().objects.filter(pk__in=user_ids, event_memberships__event=event).values_list("pk", "username")
    )

    for entry in entries:
        details = []
        for key, value in (entry.metadata or {}).items():
            if key in ("event", "event_id"):
                continue  # the page is already scoped to this event
            if key in _SUBMISSION_KEYS and value in titles:
                value = f"{titles[value]} (#{value})"
            elif key == "assignment_id" and value in assignments:
                assignment = assignments[value]
                value = f"#{value}: {assignment.submission.title}, judge {assignment.judge.username}"
            elif key == "criterion_id" and value in criteria:
                value = f"{criteria[value]} (#{value})"
            elif key == "judge_id" and value in usernames:
                value = f"{usernames[value]} (#{value})"
            details.append((key.replace("_", " "), value))
        entry.details = details
        entry.target_label = TARGET_LABELS.get(entry.target_type, entry.target_type)
        entry.badge_class = _audit_badge(entry.action)
        actor = entry.actor
        entry.actor_label = (actor.display_name or actor.username) if actor else "Anonymous visitor"
    return entries


@login_required
@require_GET
def event_audit(request, slug):
    """Read-only audit trail for one event — organizer/admin only."""
    event = get_object_or_404(Event, slug=slug)
    if not _is_organizer(request.user, event):
        return HttpResponseForbidden("Organizer access is required.")

    entries = event_audit_entries(event)
    actions = sorted(set(entries.order_by().values_list("action", flat=True)))
    action = request.GET.get("action", "").strip()
    if action not in actions:
        action = ""
    if action:
        entries = entries.filter(action=action)

    page = Paginator(entries, AUDIT_PAGE_SIZE).get_page(request.GET.get("page"))
    page.object_list = _describe_audit_entries(list(page.object_list), event)
    return render(request, "events/event_audit.html", {
        "event": event,
        "page": page,
        "actions": actions,
        "action": action,
        "action_query": f"&{urlencode({'action': action})}" if action else "",
    })


@login_required
def event_edit(request, slug):
    """Edit event — organizer only."""
    event = get_object_or_404(Event, slug=slug)
    if not _is_organizer(request.user, event):
        return HttpResponseForbidden("Only organizers can edit events.")
    if request.method == "POST":
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            form.save()
            messages.success(request, "Event updated.")
            return redirect("events:event_detail", slug=event.slug)
    else:
        form = EventForm(instance=event)
    return render(request, "events/event_form.html", {"form": form, "editing": True, "event": event})


@login_required
def track_create(request, event_slug):
    """Add a track to an event — organizer only."""
    event = get_object_or_404(Event, slug=event_slug)
    if not _is_organizer(request.user, event):
        return HttpResponseForbidden("Only organizers can add tracks.")
    if request.method == "POST":
        form = TrackForm(request.POST)
        if form.is_valid():
            track = form.save(commit=False)
            track.event = event
            track.save()
            messages.success(request, f"Track '{track.name}' added.")
            return redirect("events:event_detail", slug=event.slug)
    else:
        form = TrackForm()
    return render(request, "events/track_form.html", {"form": form, "event": event})


@login_required
def prize_create(request, event_slug):
    """Add a prize to an event — organizer only."""
    event = get_object_or_404(Event, slug=event_slug)
    if not _is_organizer(request.user, event):
        return HttpResponseForbidden("Only organizers can add prizes.")
    if request.method == "POST":
        form = PrizeForm(request.POST)
        if form.is_valid():
            prize = form.save(commit=False)
            prize.event = event
            prize.save()
            messages.success(request, f"Prize '{prize.name}' added.")
            return redirect("events:event_detail", slug=event.slug)
    else:
        form = PrizeForm()
        form.fields["track"].queryset = Track.objects.filter(event=event)
    return render(request, "events/prize_form.html", {"form": form, "event": event})
