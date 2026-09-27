"""T3 community voting and gallery-comment security tests."""
from datetime import timedelta

import pytest
from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import EventMembership, User
from apps.core.models import AuditLog
from apps.events.models import Event, Track
from apps.submissions.models import Submission
from apps.teams.models import Team, TeamMembership
from apps.voting.models import Vote
from apps.voting.views import ANONYMOUS_BALLOT_COOKIE, ANONYMOUS_BALLOT_SALT


def api_for(user):
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def add_member(user, event, role):
    return EventMembership.objects.create(user=user, event=event, role=role)


def make_submission(event, owner, track, title, status=Submission.Status.SUBMITTED):
    team = Team.objects.create(event=event, name=f"{title} team", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner)
    return Submission.objects.create(
        event=event,
        team=team,
        track=track,
        title=title,
        summary=f"Summary for {title}",
        status=status,
        submitted_at=timezone.now() if status == Submission.Status.SUBMITTED else None,
    )


@pytest.fixture
def voting_context(db):
    cache.clear()
    organizer = User.objects.create_user("vote_organizer", password="password123")
    participant = User.objects.create_user("vote_participant", password="password123")
    peer_participant = User.objects.create_user("vote_peer", password="password123")
    outsider = User.objects.create_user("vote_outsider", password="password123")
    event = Event.objects.create(
        name="T3 Voting Event",
        slug="t3-voting-event",
        created_by=organizer,
        is_published=True,
        voting_start=timezone.now() - timedelta(minutes=5),
        voting_end=timezone.now() + timedelta(minutes=5),
        voting_access=Event.VotingAccess.PUBLIC,
    )
    add_member(organizer, event, EventMembership.Role.ORGANIZER)
    add_member(participant, event, EventMembership.Role.PARTICIPANT)
    add_member(peer_participant, event, EventMembership.Role.PARTICIPANT)
    track = Track.objects.create(event=event, name="Community")
    projects = [
        make_submission(event, participant, track, f"Community Project {number}")
        for number in range(6)
    ]
    draft = make_submission(event, participant, track, "Community Draft", Submission.Status.DRAFT)
    return {
        "organizer": organizer,
        "participant": participant,
        "peer_participant": peer_participant,
        "outsider": outsider,
        "event": event,
        "track": track,
        "projects": projects,
        "draft": draft,
    }


def ballot(client, event):
    return client.get(f"/api/voting/ballot?event={event.slug}")


def vote(client, event, submission):
    return client.post(
        "/api/voting/votes",
        {"event": event.slug, "submission_id": submission.pk},
        format="json",
    )


def test_voting_window_and_configurable_access_are_enforced_server_side(voting_context):
    event = voting_context["event"]
    participant = voting_context["participant"]
    outsider = voting_context["outsider"]

    event.voting_start = timezone.now() + timedelta(minutes=1)
    event.save(update_fields=["voting_start"])
    assert ballot(APIClient(), event).status_code == 403
    assert vote(APIClient(), event, voting_context["projects"][0]).status_code == 403

    event.voting_start = timezone.now() - timedelta(minutes=5)
    event.voting_end = timezone.now() - timedelta(seconds=1)
    event.save(update_fields=["voting_start", "voting_end"])
    assert ballot(APIClient(), event).status_code == 403

    event.voting_end = timezone.now() + timedelta(minutes=5)
    event.voting_access = Event.VotingAccess.PARTICIPANTS
    event.save(update_fields=["voting_end", "voting_access"])
    assert ballot(APIClient(), event).status_code == 403
    assert ballot(api_for(outsider), event).status_code == 403
    assert ballot(api_for(participant), event).status_code == 200

    event.voting_access = Event.VotingAccess.AUTHENTICATED
    event.save(update_fields=["voting_access"])
    assert ballot(APIClient(), event).status_code == 403
    assert ballot(api_for(outsider), event).status_code == 200


def test_ballots_are_identity_randomized_and_only_include_public_submissions(voting_context):
    event = voting_context["event"]
    first = APIClient()
    second = APIClient()
    first.cookies[ANONYMOUS_BALLOT_COOKIE] = signing.dumps("ballot-one", salt=ANONYMOUS_BALLOT_SALT)
    second.cookies[ANONYMOUS_BALLOT_COOKIE] = signing.dumps("ballot-two", salt=ANONYMOUS_BALLOT_SALT)

    first_response = ballot(first, event)
    second_response = ballot(second, event)
    assert first_response.status_code == second_response.status_code == 200
    first_order = [project["id"] for project in first_response.data["projects"]]
    second_order = [project["id"] for project in second_response.data["projects"]]
    assert first_order != second_order
    assert voting_context["draft"].pk not in first_order
    assert set(first_order) == {project.pk for project in voting_context["projects"]}

    assert vote(first, event, voting_context["draft"]).status_code == 404


def test_duplicate_votes_are_blocked_by_api_and_database_constraint(voting_context):
    event = voting_context["event"]
    project = voting_context["projects"][0]
    client = APIClient()
    assert ballot(client, event).status_code == 200
    assert vote(client, event, project).status_code == 201
    assert vote(client, event, project).status_code == 409

    voter_ref = Vote.objects.get(submission=project).voter_ref
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Vote.objects.create(submission=project, voter_ref=voter_ref)


@override_settings(VOTE_RATE_LIMIT_ATTEMPTS=2, VOTE_RATE_LIMIT_WINDOW_SECONDS=60)
def test_repeated_vote_attempts_are_rate_limited(voting_context):
    cache.clear()
    event = voting_context["event"]
    client = APIClient()
    assert ballot(client, event).status_code == 200
    assert vote(client, event, voting_context["projects"][0]).status_code == 201
    assert vote(client, event, voting_context["projects"][0]).status_code == 409
    assert vote(client, event, voting_context["projects"][1]).status_code == 429


def test_active_results_are_organizer_only_and_successful_votes_are_audited(voting_context):
    event = voting_context["event"]
    participant = voting_context["participant"]
    organizer = voting_context["organizer"]
    project = voting_context["projects"][0]

    event.voting_access = Event.VotingAccess.PARTICIPANTS
    event.save(update_fields=["voting_access"])
    assert vote(api_for(participant), event, project).status_code == 201
    audit = AuditLog.objects.get(action="vote.created")
    assert audit.actor == participant
    assert audit.metadata["voter_ref"] == f"user:{participant.pk}"

    assert api_for(participant).get(f"/api/voting/results?event={event.slug}").status_code == 403
    organizer_results = api_for(organizer).get(f"/api/voting/results?event={event.slug}")
    assert organizer_results.status_code == 200
    assert organizer_results.data["results"][0]["submission_id"] == project.pk
    assert organizer_results.data["results"][0]["vote_count"] == 1

    event.voting_end = timezone.now() - timedelta(seconds=1)
    event.save(update_fields=["voting_end"])
    assert APIClient().get(f"/api/voting/results?event={event.slug}").status_code == 200


def test_comment_create_read_ownership_and_organizer_moderation(voting_context):
    event = voting_context["event"]
    project = voting_context["projects"][0]
    participant = voting_context["participant"]
    peer = voting_context["peer_participant"]
    outsider = voting_context["outsider"]
    organizer = voting_context["organizer"]
    url = f"/api/projects/{project.pk}/comments"

    assert APIClient().post(url, {"body": "Unauthenticated"}, format="json").status_code == 401
    assert api_for(outsider).post(url, {"body": "Not a member"}, format="json").status_code == 403
    created = api_for(participant).post(url, {"body": "Useful feedback"}, format="json")
    assert created.status_code == 201
    comment_id = created.data["id"]
    assert AuditLog.objects.filter(actor=participant, action="comment.created", target_id=str(comment_id)).exists()

    public_comments = APIClient().get(url)
    assert public_comments.status_code == 200
    assert public_comments.data["comments"][0]["author"] == participant.username
    assert public_comments.data["comments"][0]["body"] == "Useful feedback"
    assert "Useful feedback" in Client().get(f"/projects/{project.pk}/").content.decode()

    assert api_for(peer).patch(
        f"/api/comments/{comment_id}", {"body": "Attempted takeover"}, format="json"
    ).status_code == 403
    assert api_for(participant).patch(
        f"/api/comments/{comment_id}", {"body": "Updated feedback"}, format="json"
    ).status_code == 200
    assert api_for(peer).post(
        f"/api/comments/{comment_id}/moderation", {"is_hidden": True}, format="json"
    ).status_code == 403

    hidden = api_for(organizer).post(
        f"/api/comments/{comment_id}/moderation", {"is_hidden": True}, format="json"
    )
    assert hidden.status_code == 200
    assert hidden.data["is_hidden"] is True
    assert APIClient().get(url).data["comments"] == []
    assert "Updated feedback" not in Client().get(f"/projects/{project.pk}/").content.decode()
    assert api_for(peer).get(f"{url}?include_hidden=1").data["comments"] == []
    organizer_comments = api_for(organizer).get(f"{url}?include_hidden=1")
    assert organizer_comments.data["comments"][0]["is_hidden"] is True
    assert AuditLog.objects.filter(actor=organizer, action="comment.hidden", target_id=str(comment_id)).exists()

    unhidden = api_for(organizer).post(
        f"/api/comments/{comment_id}/moderation", {"is_hidden": False}, format="json"
    )
    assert unhidden.status_code == 200
    assert APIClient().get(url).data["comments"][0]["body"] == "Updated feedback"


def test_comments_are_limited_to_submitted_public_projects(voting_context):
    draft = voting_context["draft"]
    participant = voting_context["participant"]
    url = f"/api/projects/{draft.pk}/comments"
    assert APIClient().get(url).status_code == 404
    assert api_for(participant).post(url, {"body": "Not allowed"}, format="json").status_code == 404
