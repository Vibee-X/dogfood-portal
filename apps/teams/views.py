from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.contrib import messages
from .models import Team, TeamMembership, InviteLink
from .forms import TeamForm
from apps.events.models import Event
from apps.accounts.models import EventMembership


@login_required
def team_create(request, event_slug):
    """Create a team for an event."""
    event = get_object_or_404(Event, slug=event_slug)
    if request.method == "POST":
        form = TeamForm(request.POST)
        if form.is_valid():
            team = form.save(commit=False)
            team.event = event
            team.created_by = request.user
            team.save()
            # Creator joins the team
            TeamMembership.objects.create(team=team, user=request.user)
            # Ensure user has participant membership for this event
            EventMembership.objects.get_or_create(
                user=request.user,
                event=event,
                defaults={"role": EventMembership.Role.PARTICIPANT},
            )
            messages.success(request, f"Team '{team.name}' created.")
            return redirect("teams:team_detail", pk=team.pk)
    else:
        form = TeamForm()
    return render(request, "teams/team_form.html", {"form": form, "event": event})


@login_required
def team_detail(request, pk):
    """View team details."""
    team = get_object_or_404(Team, pk=pk)
    members = team.memberships.select_related("user").all()
    invite_links = team.invite_links.all()
    is_member = team.memberships.filter(user=request.user).exists()
    return render(request, "teams/team_detail.html", {
        "team": team,
        "members": members,
        "invite_links": invite_links,
        "is_member": is_member,
    })


@login_required
def generate_invite(request, pk):
    """Generate an invite link for a team — members only."""
    team = get_object_or_404(Team, pk=pk)
    if not team.memberships.filter(user=request.user).exists():
        return HttpResponseForbidden("Only team members can generate invite links.")
    invite = InviteLink(team=team)
    invite.save()
    messages.success(request, f"Invite link generated: {invite.code}")
    return redirect("teams:team_detail", pk=team.pk)


@login_required
def join_team(request, code):
    """Join a team via invite link."""
    invite = get_object_or_404(InviteLink, code=code)
    if not invite.is_valid:
        messages.error(request, "This invite link has expired or reached its limit.")
        return redirect("submissions:gallery")
    team = invite.team
    if team.memberships.filter(user=request.user).exists():
        messages.info(request, "You are already a member of this team.")
        return redirect("teams:team_detail", pk=team.pk)
    TeamMembership.objects.create(team=team, user=request.user)
    invite.uses_count += 1
    invite.save()
    # Ensure participant membership
    EventMembership.objects.get_or_create(
        user=request.user,
        event=team.event,
        defaults={"role": EventMembership.Role.PARTICIPANT},
    )
    messages.success(request, f"You joined team '{team.name}'!")
    return redirect("teams:team_detail", pk=team.pk)


@login_required
def leave_team(request, pk):
    """Leave a team."""
    team = get_object_or_404(Team, pk=pk)
    membership = team.memberships.filter(user=request.user).first()
    if membership:
        membership.delete()
        messages.success(request, f"You left team '{team.name}'.")
    return redirect("submissions:gallery")
