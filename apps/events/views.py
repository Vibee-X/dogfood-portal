from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.contrib import messages
from .models import Event, Track, Prize
from .forms import EventForm, TrackForm, PrizeForm
from apps.accounts.models import EventMembership


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
    """List all published events."""
    events = Event.objects.filter(is_published=True).order_by("-created_at")
    return render(request, "events/event_list.html", {"events": events})


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
    return render(request, "events/event_detail.html", {
        "event": event,
        "is_organizer": is_org,
        "tracks": tracks,
        "prizes": prizes,
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
