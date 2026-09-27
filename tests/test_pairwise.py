"""Pairwise judging model, access control, workflow, and ranking tests."""
import math

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import EventMembership, User
from apps.core.models import AuditLog
from apps.events.models import Event, Track
from apps.judging.models import JudgeAssignment, PairwiseComparison
from apps.judging.services import bradley_terry_ranking, generate_pairwise_comparisons
from apps.submissions.models import Submission
from apps.teams.models import Team, TeamMembership


def api_for(user):
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def add_member(user, event, role):
    return EventMembership.objects.create(user=user, event=event, role=role)


def make_submission(event, owner, track, title):
    team = Team.objects.create(event=event, name=f"{title} Team", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner)
    return Submission.objects.create(
        event=event,
        team=team,
        track=track,
        title=title,
        summary=f"Summary for {title}",
        description=f"Detailed description for {title}",
        status=Submission.Status.SUBMITTED,
    )


@pytest.fixture
def pairwise_context(db):
    organizer = User.objects.create_user("pair_organizer", password="password123")
    judge = User.objects.create_user("pair_judge", password="password123")
    peer = User.objects.create_user("pair_peer", password="password123")
    participant = User.objects.create_user("pair_participant", password="password123")
    event = Event.objects.create(name="Pairwise Event", slug="pairwise-event", created_by=organizer)
    for user, role in (
        (organizer, EventMembership.Role.ORGANIZER),
        (judge, EventMembership.Role.JUDGE),
        (peer, EventMembership.Role.JUDGE),
        (participant, EventMembership.Role.PARTICIPANT),
    ):
        add_member(user, event, role)
    track = Track.objects.create(event=event, name="Pairwise Track")
    owners = [User.objects.create_user(f"pair_owner_{number}", password="password123") for number in range(4)]
    for owner in owners:
        add_member(owner, event, EventMembership.Role.PARTICIPANT)
    projects = [make_submission(event, owner, track, f"Pair Project {number}") for number, owner in enumerate(owners)]
    for project in projects[:3]:
        JudgeAssignment.objects.create(event=event, judge=judge, submission=project)
    for project in projects[:2]:
        JudgeAssignment.objects.create(event=event, judge=peer, submission=project)
    return {
        "organizer": organizer,
        "judge": judge,
        "peer": peer,
        "participant": participant,
        "event": event,
        "track": track,
        "projects": projects,
    }


def test_pairwise_model_canonicalizes_pairs_and_enforces_integrity(pairwise_context):
    event = pairwise_context["event"]
    judge = pairwise_context["judge"]
    first, second, third, _ = pairwise_context["projects"]

    comparison = PairwiseComparison(event=event, judge=judge, submission_a=second, submission_b=first)
    comparison.full_clean()
    assert comparison.submission_a_id == first.pk
    assert comparison.submission_b_id == second.pk
    comparison.save()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PairwiseComparison.objects.create(event=event, judge=judge, submission_a=second, submission_b=first)
    with pytest.raises(ValidationError):
        PairwiseComparison(event=event, judge=judge, submission_a=first, submission_b=first).full_clean()
    with pytest.raises(ValidationError):
        PairwiseComparison(
            event=event, judge=judge, submission_a=first, submission_b=second, winner=third
        ).full_clean()

    # Database constraints still protect direct writes that skip model validation.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PairwiseComparison.objects.bulk_create([
                PairwiseComparison(event=event, judge=judge, submission_a=first, submission_b=first)
            ])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PairwiseComparison.objects.bulk_create([
                PairwiseComparison(event=event, judge=judge, submission_a=first, submission_b=third, winner=second)
            ])


def test_pairwise_model_rejects_wrong_event_and_unassigned_projects(pairwise_context):
    event = pairwise_context["event"]
    judge = pairwise_context["judge"]
    first, second, _, unassigned = pairwise_context["projects"]
    other_event = Event.objects.create(name="Other Pairwise", slug="other-pairwise", created_by=judge)
    other_track = Track.objects.create(event=other_event, name="Other Track")
    add_member(judge, other_event, EventMembership.Role.JUDGE)
    other_owner = User.objects.create_user("other_pair_owner", password="password123")
    add_member(other_owner, other_event, EventMembership.Role.PARTICIPANT)
    other_project = make_submission(other_event, other_owner, other_track, "Other Project")
    JudgeAssignment.objects.create(event=other_event, judge=judge, submission=other_project)

    with pytest.raises(ValidationError):
        PairwiseComparison(event=event, judge=judge, submission_a=first, submission_b=other_project).full_clean()
    with pytest.raises(ValidationError):
        PairwiseComparison(event=event, judge=judge, submission_a=first, submission_b=unassigned).full_clean()
    TeamMembership.objects.create(team=second.team, user=judge)
    with pytest.raises(ValidationError):
        PairwiseComparison(event=event, judge=judge, submission_a=first, submission_b=second).full_clean()
    assert second.pk != other_project.pk


def test_pair_generation_is_deterministic_idempotent_and_uses_only_authorized_assignments(pairwise_context):
    event = pairwise_context["event"]
    judge = pairwise_context["judge"]
    projects = pairwise_context["projects"]
    # This deliberately invalid legacy-style assignment is not eligible: the
    # judge joined the team's project after assignment creation.
    TeamMembership.objects.create(team=projects[3].team, user=judge)
    JudgeAssignment.objects.create(event=event, judge=judge, submission=projects[3])

    first = generate_pairwise_comparisons(event)
    pairs = list(
        PairwiseComparison.objects.filter(event=event, judge=judge).values_list("submission_a_id", "submission_b_id")
    )
    assert first["created"] == 4  # three judge pairs plus one peer pair
    assert first["candidate_pairs"] == 4
    assert pairs == [(projects[0].pk, projects[1].pk), (projects[0].pk, projects[2].pk), (projects[1].pk, projects[2].pk)]
    assert all(projects[3].pk not in pair for pair in pairs)
    assert all(first_id < second_id for first_id, second_id in pairs)
    assert AuditLog.objects.filter(action="pairwise.comparison.assigned").count() == 4

    second = generate_pairwise_comparisons(event)
    assert second["created"] == 0
    assert list(
        PairwiseComparison.objects.filter(event=event, judge=judge).values_list("submission_a_id", "submission_b_id")
    ) == pairs


def test_pairwise_api_enforces_judge_ownership_event_scope_and_audit(pairwise_context):
    organizer = pairwise_context["organizer"]
    judge = pairwise_context["judge"]
    peer = pairwise_context["peer"]
    participant = pairwise_context["participant"]
    event = pairwise_context["event"]
    first, second, third, _ = pairwise_context["projects"]

    assert api_for(participant).post("/api/judging/pairwise/generate", {"event": event.slug}, format="json").status_code == 403
    generated = api_for(organizer).post("/api/judging/pairwise/generate", {"event": event.slug}, format="json")
    assert generated.status_code == 200

    pending = api_for(judge).get(f"/api/judging/pairwise/next?event={event.slug}")
    assert pending.status_code == 200
    comparison_id = pending.data["id"]
    assert {pending.data["submission_a"]["id"], pending.data["submission_b"]["id"]} == {first.pk, second.pk}
    assert api_for(participant).get(f"/api/judging/pairwise/next?event={event.slug}").status_code == 403
    assert api_for(organizer).get(f"/api/judging/pairwise/next?event={event.slug}").status_code == 403

    assert api_for(judge).get(
        f"/api/judging/pairwise/comparisons?event={event.slug}&judge={peer.username}"
    ).status_code == 403
    assert api_for(peer).post(
        "/api/judging/pairwise/comparisons",
        {"event": event.slug, "comparison_id": comparison_id, "winner_id": first.pk},
        format="json",
    ).status_code == 403
    assert api_for(participant).post(
        "/api/judging/pairwise/comparisons",
        {"event": event.slug, "comparison_id": comparison_id, "winner_id": first.pk},
        format="json",
    ).status_code == 403
    assert api_for(judge).post(
        "/api/judging/pairwise/comparisons",
        {"event": event.slug, "comparison_id": comparison_id, "winner_id": third.pk},
        format="json",
    ).status_code == 400

    completed = api_for(judge).post(
        "/api/judging/pairwise/comparisons",
        {"event": event.slug, "comparison_id": comparison_id, "winner_id": first.pk},
        format="json",
    )
    assert completed.status_code == 200
    assert completed.data["winner_id"] == first.pk
    assert AuditLog.objects.filter(actor=judge, action="pairwise.comparison.completed", target_id=str(comparison_id)).exists()
    assert api_for(judge).post(
        "/api/judging/pairwise/comparisons",
        {"event": event.slug, "comparison_id": comparison_id, "winner_id": first.pk},
        format="json",
    ).status_code == 409

    own_completed = api_for(judge).get(f"/api/judging/pairwise/comparisons?event={event.slug}&completed=1")
    assert own_completed.status_code == 200
    assert own_completed.data["comparisons"][0]["id"] == comparison_id
    assert "judge" not in own_completed.data["comparisons"][0]
    organizer_list = api_for(organizer).get(
        f"/api/judging/pairwise/comparisons?event={event.slug}&judge={judge.username}&completed=1"
    )
    assert organizer_list.status_code == 200
    assert organizer_list.data["comparisons"][0]["judge"] == judge.username

    other_event = Event.objects.create(name="Unrelated", slug="unrelated-pairwise", created_by=organizer)
    assert api_for(judge).get(f"/api/judging/pairwise/next?event={other_event.slug}").status_code == 403


def test_pairwise_judge_ui_advances_with_htmx_and_is_role_restricted(pairwise_context):
    event = pairwise_context["event"]
    judge = pairwise_context["judge"]
    organizer = pairwise_context["organizer"]
    participant = pairwise_context["participant"]
    generate_pairwise_comparisons(event)
    comparison = PairwiseComparison.objects.filter(event=event, judge=judge, winner__isnull=True).first()
    url = f"/events/{event.slug}/judging/pairwise/"

    client = Client()
    client.login(username=judge.username, password="password123")
    page = client.get(url)
    assert page.status_code == 200
    assert b"Project A" in page.content and b"Choose A" in page.content and b"hx-post" in page.content
    advanced = client.post(
        url,
        {"comparison_id": comparison.pk, "winner_id": comparison.submission_a_id},
        HTTP_HX_REQUEST="true",
    )
    assert advanced.status_code == 200
    assert PairwiseComparison.objects.get(pk=comparison.pk).winner_id == comparison.submission_a_id
    assert b"Choose A" in advanced.content or b"All assigned comparisons are complete" in advanced.content

    client.logout()
    client.login(username=participant.username, password="password123")
    assert client.get(url).status_code == 403
    client.logout()
    client.login(username=organizer.username, password="password123")
    assert client.get(url).status_code == 403


def test_bradley_terry_ranking_orders_completed_connected_comparisons(pairwise_context):
    event = pairwise_context["event"]
    judge = pairwise_context["judge"]
    first, second, third, _ = pairwise_context["projects"]
    PairwiseComparison.objects.create(event=event, judge=judge, submission_a=first, submission_b=second, winner=first)
    PairwiseComparison.objects.create(event=event, judge=judge, submission_a=first, submission_b=third, winner=first)
    PairwiseComparison.objects.create(event=event, judge=judge, submission_a=second, submission_b=third, winner=second)

    ranking = bradley_terry_ranking(event)
    assert ranking["method"] == "bradley-terry-bounded-mle-v1"
    assert len(ranking["components"]) == 1
    assert [item["submission_id"] for item in ranking["rankings"][:3]] == [first.pk, second.pk, third.pk]
    assert all(math.isfinite(item["strength"]) for item in ranking["rankings"][:3])
    assert all(0 <= item["win_probability_vs_component_anchor"] <= 1 for item in ranking["rankings"][:3])

    response = api_for(pairwise_context["organizer"]).get(f"/api/judging/pairwise/rankings?event={event.slug}")
    assert response.status_code == 200
    assert api_for(pairwise_context["participant"]).get(
        f"/api/judging/pairwise/rankings?event={event.slug}"
    ).status_code == 403


def test_bradley_terry_handles_incomplete_disconnected_and_empty_graphs(pairwise_context):
    event = pairwise_context["event"]
    judge = pairwise_context["judge"]
    first, second, third, fourth = pairwise_context["projects"]

    empty = bradley_terry_ranking(event)
    assert empty["components"] == []
    assert all(item["rank_in_component"] is None for item in empty["rankings"])

    # A single one-sided decision is a separated graph. Bounded optimization
    # must remain finite, and the unreviewed project remains explicitly unranked.
    PairwiseComparison.objects.create(event=event, judge=judge, submission_a=first, submission_b=second, winner=first)
    incomplete = bradley_terry_ranking(event)
    by_id = {item["submission_id"]: item for item in incomplete["rankings"]}
    assert len(incomplete["components"]) == 1
    assert by_id[first.pk]["rank_in_component"] == 1
    assert math.isfinite(by_id[first.pk]["strength"])
    assert by_id[third.pk]["rank_in_component"] is None

    # Add a separate valid assignment and comparison. The two components get
    # independent anchors and therefore no invented cross-component ordering.
    JudgeAssignment.objects.create(event=event, judge=judge, submission=fourth)
    PairwiseComparison.objects.create(event=event, judge=judge, submission_a=third, submission_b=fourth, winner=third)
    disconnected = bradley_terry_ranking(event)
    by_id = {item["submission_id"]: item for item in disconnected["rankings"]}
    assert len(disconnected["components"]) == 2
    assert by_id[first.pk]["component_id"] != by_id[third.pk]["component_id"]
    assert by_id[first.pk]["rank_in_component"] == by_id[third.pk]["rank_in_component"] == 1
