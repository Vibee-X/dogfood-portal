"""Judge scoring web page and the seed's demo assignments."""
import json
from pathlib import Path

import pytest
from django.test import Client
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import EventMembership, User
from apps.core.models import AuditLog
from apps.events.models import Event, Track
from apps.judging.models import JudgeAssignment, NormalizationRun, Rubric, RubricCriterion, Score
from apps.judging.services import judge_can_review_submission
from apps.submissions.models import Submission
from apps.teams.models import Team, TeamMembership

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "fixtures.json"


def web_for(user):
    client = Client()
    client.force_login(user)
    return client


def member(user, event, role):
    return EventMembership.objects.create(user=user, event=event, role=role)


def project(event, owner, track, title):
    team = Team.objects.create(event=event, name=f"{title} Team", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner)
    return Submission.objects.create(event=event, team=team, track=track, title=title, status=Submission.Status.SUBMITTED)


@pytest.fixture
def console(db):
    organizer = User.objects.create_user("console_organizer", password="password123")
    event = Event.objects.create(name="Console Event", slug="console-event", created_by=organizer, is_published=True)
    member(organizer, event, EventMembership.Role.ORGANIZER)
    judge = User.objects.create_user("console_judge", password="password123")
    peer = User.objects.create_user("console_peer", password="password123")
    participant = User.objects.create_user("console_participant", password="password123")
    member(judge, event, EventMembership.Role.JUDGE)
    member(peer, event, EventMembership.Role.JUDGE)
    member(participant, event, EventMembership.Role.PARTICIPANT)
    track = Track.objects.create(event=event, name="Main")
    rubric = Rubric.objects.create(event=event, name="Core", is_active=True)
    quality = RubricCriterion.objects.create(rubric=rubric, name="quality", weight=2, max_score=5)
    impact = RubricCriterion.objects.create(rubric=rubric, name="impact", weight=1, max_score=10)
    mine = project(event, participant, track, "Judge Own Project")
    theirs = project(event, User.objects.create_user("console_owner", password="password123"), track, "Peer Only Project")
    return {
        "event": event, "organizer": organizer, "judge": judge, "peer": peer, "participant": participant,
        "quality": quality, "impact": impact,
        "mine": JudgeAssignment.objects.create(event=event, judge=judge, submission=mine),
        "theirs": JudgeAssignment.objects.create(event=event, judge=peer, submission=theirs),
        "url": f"/events/{event.slug}/judging/scores/",
    }


def post_scores(client, url, assignment, **values):
    data = {"assignment_id": assignment.pk}
    data.update({f"criterion_{criterion.pk}": value for criterion, value in values.values()})
    return client.post(url, data)


def test_judge_sees_only_their_own_assignments(console):
    response = web_for(console["judge"]).get(console["url"])
    assert response.status_code == 200
    assert [row["assignment"].pk for row in response.context["rows"]] == [console["mine"].pk]
    content = response.content.decode()
    assert "Judge Own Project" in content
    assert "Peer Only Project" not in content
    assert f'value="{console["theirs"].pk}"' not in content


def test_saving_scores_updates_status_and_writes_the_audit_log(console):
    client, url, mine = web_for(console["judge"]), console["url"], console["mine"]
    quality, impact = console["quality"], console["impact"]

    first = post_scores(client, url, mine, q=(quality, "4"))
    assert first.status_code == 302
    assert first["Location"].endswith(f"#assignment-{mine.pk}")
    score = Score.objects.get(assignment=mine, criterion=quality)
    assert score.value == 4
    created = AuditLog.objects.get(action="score.created", target_id=str(score.pk))
    assert created.actor == console["judge"]
    assert created.metadata["old_value"] is None and created.metadata["new_value"] == 4
    mine.refresh_from_db()
    assert mine.status == JudgeAssignment.Status.IN_PROGRESS
    assert client.get(url).context["rows"][0]["state"] == "partial"

    second = post_scores(client, url, mine, q=(quality, "5"), i=(impact, "7"))
    assert second.status_code == 302
    updated = AuditLog.objects.get(action="score.updated", target_id=str(score.pk))
    assert updated.metadata["old_value"] == 4 and updated.metadata["new_value"] == 5
    assert AuditLog.objects.filter(action="score.created", actor=console["judge"]).count() == 2
    mine.refresh_from_db()
    assert mine.status == JudgeAssignment.Status.COMPLETED
    row = client.get(url).context["rows"][0]
    assert row["state"] == "complete"
    assert [field["value"] for field in row["fields"]] == [5, 7]  # prefilled with saved values


def test_a_judge_cannot_score_another_judges_assignment(console):
    response = post_scores(web_for(console["judge"]), console["url"], console["theirs"], q=(console["quality"], "3"))
    assert response.status_code == 403
    assert "You can only score your own assignment." in response.content.decode()
    assert "Peer Only Project" not in response.content.decode()
    assert not Score.objects.filter(assignment=console["theirs"]).exists()
    assert not AuditLog.objects.exists()

    # Criteria from another event's rubric are not fields on this page, so
    # posting one saves nothing.
    other = Event.objects.create(name="Other", slug="other-console", created_by=console["organizer"])
    foreign = RubricCriterion.objects.create(rubric=Rubric.objects.create(event=other, name="X"), name="x", max_score=5)
    ignored = post_scores(web_for(console["judge"]), console["url"], console["mine"], f=(foreign, "3"))
    assert ignored.status_code == 400
    assert not Score.objects.exists()


def test_out_of_range_values_are_rejected_and_the_form_saves_nothing(console):
    client, url, mine, quality, impact = (
        web_for(console["judge"]), console["url"], console["mine"], console["quality"], console["impact"],
    )
    for bad, message in (("6", "Value must be between 1 and 5."), ("0", "Value must be between 1 and 5."),
                         ("abc", "value must be an integer.")):
        response = post_scores(client, url, mine, q=(quality, bad))
        assert response.status_code == 400
        assert message in response.content.decode()
        assert response.context["rows"][0]["fields"][0]["value"] == bad  # the entered value is kept
    # One valid and one invalid value: the whole form is rolled back.
    response = post_scores(client, url, mine, q=(quality, "4"), i=(impact, "11"))
    assert response.status_code == 400
    assert "Value must be between 1 and 10." in response.content.decode()
    assert not Score.objects.exists()
    assert not AuditLog.objects.exists()


def test_page_and_api_share_the_same_rule_and_message(console):
    token, _ = Token.objects.get_or_create(user=console["judge"])
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    api_response = api.post(
        "/api/judge/scores",
        {"assignment_id": console["mine"].pk, "criterion_id": console["quality"].pk, "value": 9},
        format="json",
    )
    page_response = post_scores(web_for(console["judge"]), console["url"], console["mine"], q=(console["quality"], "9"))
    assert api_response.status_code == page_response.status_code == 400
    assert api_response.data["value"] == ["Value must be between 1 and 5."]
    assert "Value must be between 1 and 5." in page_response.content.decode()


def test_organizers_participants_and_visitors_cannot_use_the_page(console):
    url = console["url"]
    visitor = Client().get(url)
    assert visitor.status_code == 302 and visitor["Location"].startswith("/accounts/login/")
    for role in ("participant", "organizer"):
        client = web_for(console[role])
        assert client.get(url).status_code == 403
        assert post_scores(client, url, console["mine"], q=(console["quality"], "3")).status_code == 403
    assert not Score.objects.exists()
    assert not AuditLog.objects.exists()


def test_event_page_links_judges_to_the_scoring_page(console):
    detail = f"/events/{console['event'].slug}/"
    assert console["url"] in web_for(console["judge"]).get(detail).content.decode()
    for role in ("participant", "organizer"):
        assert console["url"] not in web_for(console[role]).get(detail).content.decode()


@pytest.mark.django_db
def test_seed_demo_assignments_are_valid_idempotent_and_leave_the_fixture_snapshot_alone():
    from scripts.seed import DEMO_BATCH_ID, seed, seed_demo_assignments

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    seed()
    event = Event.objects.get(slug=fixture["event"]["id"])
    seed_demo_assignments()
    seed_demo_assignments()

    demo = JudgeAssignment.objects.filter(event=event, batch_id=DEMO_BATCH_ID).select_related("judge", "submission")
    assert demo.count() == 8
    assert {a.judge.username for a in demo} == {"judge_a", "judge_b"}
    assert len({(a.judge_id, a.submission_id) for a in demo}) == 8
    assert all(judge_can_review_submission(a.judge, event, a.submission) for a in demo)
    assert JudgeAssignment.objects.filter(event=event).count() == len(fixture["scores"]) + 8
    assert all(
        JudgeAssignment.objects.filter(submission=s).count() >= event.reviews_per_submission
        for s in Submission.objects.filter(event=event)
    )
    run = NormalizationRun.objects.get(event=event)
    assert len(run.snapshot["assignments"]) == len(fixture["scores"])
