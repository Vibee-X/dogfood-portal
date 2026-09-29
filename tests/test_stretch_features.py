"""Bounded T4-style usability features: safe embeds and certificates."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import EventMembership, User
from apps.core.models import AuditLog, Certificate
from apps.events.models import Event, Track
from apps.submissions.models import Submission
from apps.teams.models import Team, TeamMembership


@pytest.fixture
def stretch_context(db):
    organizer = User.objects.create_user("stretch_organizer", password="password123")
    participant = User.objects.create_user("stretch_participant", password="password123")
    outsider = User.objects.create_user("stretch_outsider", password="password123")
    event = Event.objects.create(
        name="Stretch Event",
        slug="stretch-event",
        created_by=organizer,
        is_published=True,
    )
    EventMembership.objects.create(user=organizer, event=event, role=EventMembership.Role.ORGANIZER)
    EventMembership.objects.create(user=participant, event=event, role=EventMembership.Role.PARTICIPANT)
    track = Track.objects.create(event=event, name="Build")
    team = Team.objects.create(event=event, name="Stretch Team", created_by=participant)
    TeamMembership.objects.create(team=team, user=participant)
    submitted = Submission.objects.create(
        event=event,
        team=team,
        track=track,
        title="Published Stretch Project",
        summary="A project visible in the embed and certificate.",
        status=Submission.Status.SUBMITTED,
        submitted_at=timezone.now(),
    )
    draft = Submission.objects.create(
        event=event,
        team=team,
        track=track,
        title="Private Stretch Draft",
        status=Submission.Status.DRAFT,
    )
    return {
        "event": event,
        "participant": participant,
        "outsider": outsider,
        "submitted": submitted,
        "draft": draft,
    }


@pytest.mark.django_db
def test_embed_gallery_is_public_frameable_and_read_only(stretch_context, client):
    event = stretch_context["event"]
    response = client.get(reverse("submissions:event_embed_gallery", args=[event.slug]))

    assert response.status_code == 200
    assert b"Published Stretch Project" in response.content
    assert b"Private Stretch Draft" not in response.content
    assert "X-Frame-Options" not in response.headers
    assert reverse("submissions:project_detail", args=[stretch_context["submitted"].pk]).encode() not in response.content


@pytest.mark.django_db
def test_embed_gallery_hides_unpublished_events(stretch_context, client):
    event = stretch_context["event"]
    event.is_published = False
    event.save(update_fields=["is_published"])

    assert client.get(reverse("submissions:event_embed_gallery", args=[event.slug])).status_code == 404


@pytest.mark.django_db
def test_participation_certificate_is_member_only_idempotent_and_verifiable(stretch_context, client):
    event = stretch_context["event"]
    participant = stretch_context["participant"]
    issue_url = reverse("core:certificate_issue", args=[event.slug])

    assert client.get(issue_url).status_code == 302
    client.force_login(participant)
    first_issue = client.post(issue_url)
    assert first_issue.status_code == 302

    certificate = Certificate.objects.get(event=event, user=participant, type="participation")
    assert certificate.team.name == "Stretch Team"
    assert first_issue.url == reverse("core:certificate_verify", args=[certificate.verification_code])
    assert AuditLog.objects.filter(action="certificate.issued", target_id=str(certificate.pk)).count() == 1

    second_issue = client.post(issue_url)
    assert second_issue.status_code == 302
    assert second_issue.url == first_issue.url
    assert Certificate.objects.filter(event=event, user=participant, type="participation").count() == 1
    assert AuditLog.objects.filter(action="certificate.issued").count() == 1

    client.logout()
    verification = client.get(first_issue.url)
    assert verification.status_code == 200
    assert participant.username.encode() in verification.content
    assert b"Published Stretch Project" in verification.content


@pytest.mark.django_db
def test_certificate_cannot_be_issued_by_an_outsider(stretch_context, client):
    event = stretch_context["event"]
    client.force_login(stretch_context["outsider"])

    response = client.post(reverse("core:certificate_issue", args=[event.slug]))

    assert response.status_code == 403
    assert Certificate.objects.count() == 0


@pytest.mark.django_db
def test_certificate_issue_requires_login_csrf_and_active_membership(stretch_context, client):
    event = stretch_context["event"]
    issue_url = reverse("core:certificate_issue", args=[event.slug])

    anonymous = client.post(issue_url)
    assert anonymous.status_code == 302
    assert Certificate.objects.count() == 0

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(stretch_context["participant"])
    assert csrf_client.post(issue_url).status_code == 403
    assert Certificate.objects.count() == 0

    invited = User.objects.create_user("stretch_invited", password="password123")
    EventMembership.objects.create(
        user=invited,
        event=event,
        role=EventMembership.Role.PARTICIPANT,
        status=EventMembership.Status.INVITED,
    )
    client.force_login(invited)
    assert client.post(issue_url).status_code == 403
    assert Certificate.objects.count() == 0
