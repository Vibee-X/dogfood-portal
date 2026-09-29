from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.db.models import Count, Q
from .models import Event, Track, Prize
from .forms import EventForm, TrackForm, PrizeForm
from apps.accounts.models import EventMembership
from apps.core.models import Certificate
from apps.submissions.models import Submission


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


def event_list(request):
    """List all published events, optionally filtered by ?status=."""
    events = (
        Event.objects.filter(is_published=True)
        .annotate(
            project_count=Count(
                "submissions",
                filter=Q(submissions__status=Submission.Status.SUBMITTED),
                distinct=True,
            ),
            track_count=Count("tracks", distinct=True),
        )
        .order_by("-created_at")
    )
    status_filter = request.GET.get("status", "").strip().lower()
    if status_filter not in Event.STATUS_LABELS:
        status_filter = ""
    if status_filter:
        # Status is derived in Python from the event's dates (Event.status),
        # so filter with the same rule the badges use.
        events = [event for event in events if event.status == status_filter]
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
        "tracks": tracks,
        "prizes": prizes,
        "can_request_certificate": can_request_certificate,
        "participation_certificate": participation_certificate,
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
