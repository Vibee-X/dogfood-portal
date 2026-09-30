"""Organizer audit-log page: event scoping, access control, filter, pagination."""
import pytest
from django.test import Client
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import EventMembership, User
from apps.core.models import AuditLog, Certificate
from apps.events.models import Event, Track
from apps.judging.models import JudgeAssignment, PairwiseComparison, Rubric, RubricCriterion
from apps.submissions.models import Submission
from apps.teams.models import Team, TeamMembership
from apps.voting.models import Comment, Vote


def api_for(user):
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def web_for(user):
    client = Client()
    client.force_login(user)
    return client


def member(user, event, role):
    return EventMembership.objects.create(user=user, event=event, role=role)


def project(event, owner, track, title):
    team = Team.objects.create(event=event, name=f"{title} Team", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner)
    return Submission.objects.create(
        event=event, team=team, track=track, title=title, status=Submission.Status.SUBMITTED,
    )


def scored_event(prefix):
    """An event with an organizer, judge, participant, two projects and one API-written score."""
    organizer = User.objects.create_user(f"{prefix}_organizer", password="password123")
    event = Event.objects.create(name=f"{prefix} Event", slug=f"{prefix}-event", created_by=organizer, is_published=True)
    member(organizer, event, EventMembership.Role.ORGANIZER)
    judge = User.objects.create_user(f"{prefix}_judge", password="password123")
    member(judge, event, EventMembership.Role.JUDGE)
    participant = User.objects.create_user(f"{prefix}_participant", password="password123")
    member(participant, event, EventMembership.Role.PARTICIPANT)
    track = Track.objects.create(event=event, name="Main")
    criterion = RubricCriterion.objects.create(
        rubric=Rubric.objects.create(event=event, name="Core", is_active=True), name="Quality", max_score=5,
    )
    first = project(event, participant, track, f"{prefix} First")
    second = project(event, User.objects.create_user(f"{prefix}_owner2", password="password123"), track, f"{prefix} Second")
    assignment = JudgeAssignment.objects.create(event=event, judge=judge, submission=first)
    JudgeAssignment.objects.create(event=event, judge=judge, submission=second)
    response = api_for(judge).post(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": criterion.pk, "value": 4}, format="json",
    )
    assert response.status_code == 201
    score_entry = AuditLog.objects.get(action="score.created", target_id=str(response.data["id"]))
    return {
        "event": event, "organizer": organizer, "judge": judge, "participant": participant,
        "first": first, "second": second, "score_id": response.data["id"], "score_entry": score_entry,
    }


@pytest.fixture
def audit_context(db):
    a = scored_event("alpha")
    b = scored_event("beta")
    event = a["event"]

    # One entry per remaining target type, all pointing at rows in event A.
    vote = Vote.objects.create(submission=a["first"], voter_ref="user:test")
    comment = Comment.objects.create(submission=a["first"], user=a["participant"], body="Nice")
    certificate = Certificate.objects.create(
        event=event, user=a["participant"], type="participation", verification_code="AUDIT-TEST-1",
    )
    low, high = sorted([a["first"], a["second"]], key=lambda s: s.pk)
    comparison = PairwiseComparison.objects.create(event=event, judge=a["judge"], submission_a=low, submission_b=high)
    a_entries = [a["score_entry"]] + [
        AuditLog.objects.create(actor=actor, action=action, target_type=target_type, target_id=str(pk), metadata=metadata)
        for actor, action, target_type, pk, metadata in [
            (None, "vote.created", "Vote", vote.pk, {"event": event.slug, "submission_id": a["first"].pk}),
            (a["participant"], "comment.created", "Comment", comment.pk, {"submission_id": a["first"].pk}),
            (a["participant"], "certificate.issued", "certificate", certificate.pk, {"event_id": event.pk}),
            (a["organizer"], "pairwise.comparison.assigned", "PairwiseComparison", comparison.pk, {"event": event.slug}),
        ]
    ]

    # Traps: metadata names event A's slug, but the target is in event B;
    # and an entry whose target row does not exist.
    slug_trap = AuditLog.objects.create(
        actor=b["judge"], action="score.updated", target_type="Score", target_id=str(b["score_id"]),
        metadata={"event": event.slug},
    )
    orphan = AuditLog.objects.create(
        actor=a["judge"], action="score.updated", target_type="Score", target_id="999999",
        metadata={"event": event.slug},
    )
    return {"a": a, "b": b, "a_entries": a_entries, "slug_trap": slug_trap, "orphan": orphan}


def page_ids(response):
    return {entry.pk for entry in response.context["page"].object_list}


def test_organizer_sees_only_their_events_entries(audit_context):
    a, b = audit_context["a"], audit_context["b"]
    response = web_for(a["organizer"]).get(f"/events/{a['event'].slug}/audit/")
    assert response.status_code == 200
    assert page_ids(response) == {entry.pk for entry in audit_context["a_entries"]}
    content = response.content.decode()
    assert "score.created" in content and "comment.created" in content and "certificate.issued" in content

    other = web_for(b["organizer"]).get(f"/events/{b['event'].slug}/audit/")
    assert other.status_code == 200
    # Event B's own score entry plus the slug-trap entry, which targets B's score.
    assert page_ids(other) == {b["score_entry"].pk, audit_context["slug_trap"].pk}
    assert audit_context["orphan"].pk not in page_ids(response) | page_ids(other)


def test_audit_page_blocks_everyone_but_the_events_organizers(audit_context):
    a, b = audit_context["a"], audit_context["b"]
    url = f"/events/{a['event'].slug}/audit/"
    anonymous = Client().get(url)
    assert anonymous.status_code == 302
    assert anonymous["Location"].startswith("/accounts/login/")
    assert web_for(a["judge"]).get(url).status_code == 403
    assert web_for(a["participant"]).get(url).status_code == 403
    assert web_for(b["organizer"]).get(url).status_code == 403
    assert web_for(a["organizer"]).post(url).status_code == 405


def test_audit_filter_and_pagination(audit_context):
    a = audit_context["a"]
    for value in range(30):
        AuditLog.objects.create(
            actor=a["judge"], action="score.updated", target_type="Score", target_id=str(a["score_id"]),
            metadata={"old_value": value, "new_value": value + 1},
        )
    client = web_for(a["organizer"])
    url = f"/events/{a['event'].slug}/audit/"

    first = client.get(url)
    assert first.context["page"].paginator.count == 35
    assert len(first.context["page"].object_list) == 25
    assert len(client.get(url, {"page": 2}).context["page"].object_list) == 10

    filtered = client.get(url, {"action": "score.updated"})
    assert filtered.context["page"].paginator.count == 30
    assert {entry.action for entry in filtered.context["page"].object_list} == {"score.updated"}
    assert "action=score.updated" in filtered.content.decode()  # kept in pagination links

    ignored = client.get(url, {"action": "not.a.real.action"})
    assert ignored.context["page"].paginator.count == 35


def test_audit_link_is_shown_to_organizers_only(audit_context):
    a = audit_context["a"]
    audit_url = f"/events/{a['event'].slug}/audit/"
    detail = f"/events/{a['event'].slug}/"
    assert audit_url in web_for(a["organizer"]).get(detail).content.decode()
    assert audit_url not in web_for(a["participant"]).get(detail).content.decode()
    assert audit_url not in web_for(a["judge"]).get(detail).content.decode()
    assert audit_url in web_for(a["organizer"]).get(f"/events/{a['event'].slug}/judging/progress/").content.decode()
