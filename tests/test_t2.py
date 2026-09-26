"""T2 judging integrity tests: scope, assignment, scoring, normalization, and export."""
import csv
import io
import math

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import EventMembership, User
from apps.core.models import AuditLog
from apps.events.models import Event, Track
from apps.judging.models import JudgeAssignment, JudgeTrack, NormalizationRun, Rubric, RubricCriterion, Score
from apps.judging.services import generate_assignments, run_normalization
from apps.submissions.models import Submission
from apps.teams.models import Team, TeamMembership


def api_for(user):
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def member(user, event, role):
    return EventMembership.objects.create(user=user, event=event, role=role)


def submission(event, creator, track, title):
    team = Team.objects.create(event=event, name=f"{title} Team", created_by=creator)
    TeamMembership.objects.create(team=team, user=creator)
    return Submission.objects.create(
        event=event,
        team=team,
        track=track,
        title=title,
        status=Submission.Status.SUBMITTED,
    )


@pytest.fixture
def t2_context(db):
    organizer = User.objects.create_user("organizer", password="password123")
    event = Event.objects.create(name="T2 Event", slug="t2-event", created_by=organizer, reviews_per_submission=3)
    member(organizer, event, EventMembership.Role.ORGANIZER)
    track_a = Track.objects.create(event=event, name="Track A")
    track_b = Track.objects.create(event=event, name="Track B")
    rubric = Rubric.objects.create(event=event, name="Core", is_active=True)
    criterion = RubricCriterion.objects.create(rubric=rubric, name="Quality", weight=2, max_score=5)
    return organizer, event, track_a, track_b, rubric, criterion


def test_rubric_configuration_is_organizer_controlled_and_validated(t2_context):
    organizer, event, _, _, _, _ = t2_context
    participant = User.objects.create_user("participant", password="password123")
    member(participant, event, EventMembership.Role.PARTICIPANT)

    denied = api_for(participant).post("/api/judging/rubrics", {"event": event.slug, "name": "Denied"}, format="json")
    assert denied.status_code == 403

    client = api_for(organizer)
    response = client.post("/api/judging/rubrics", {"event": event.slug, "name": "Product"}, format="json")
    assert response.status_code == 201
    rubric_id = response.data["id"]
    created = client.post(
        f"/api/judging/rubrics/{rubric_id}/criteria",
        {"name": "Impact", "description": "Real-world value", "weight": 3.5, "max_score": 7},
        format="json",
    )
    assert created.status_code == 201
    assert created.data["weight"] == 3.5
    assert created.data["max_score"] == 7
    invalid = client.post(
        f"/api/judging/rubrics/{rubric_id}/criteria",
        {"name": "Broken", "weight": 0, "max_score": 0},
        format="json",
    )
    assert invalid.status_code == 400


def test_organizer_invites_judge_with_event_track_scope(t2_context):
    organizer, event, track_a, _, _, _ = t2_context
    other_event = Event.objects.create(name="Elsewhere", slug="elsewhere", created_by=organizer)
    other_track = Track.objects.create(event=other_event, name="Wrong Event")
    candidate = User.objects.create_user("candidate", password="password123")
    participant = User.objects.create_user("participant", password="password123")
    member(participant, event, EventMembership.Role.PARTICIPANT)

    denied = api_for(participant).post(
        "/api/judging/judges/invite", {"event": event.slug, "username": candidate.username, "track_ids": [track_a.pk]}, format="json"
    )
    assert denied.status_code == 403
    response = api_for(organizer).post(
        "/api/judging/judges/invite", {"event": event.slug, "username": candidate.username, "track_ids": [track_a.pk]}, format="json"
    )
    assert response.status_code == 201
    assert EventMembership.objects.get(user=candidate, event=event).role == EventMembership.Role.JUDGE
    assert JudgeTrack.objects.filter(event=event, judge=candidate, track=track_a).exists()
    wrong_event_track = api_for(organizer).post(
        "/api/judging/judges/invite", {"event": event.slug, "username": candidate.username, "track_ids": [other_track.pk]}, format="json"
    )
    assert wrong_event_track.status_code == 400


@pytest.fixture
def assignment_context(t2_context):
    organizer, event, track_a, track_b, rubric, criterion = t2_context
    judges = []
    for number in range(5):
        judge = User.objects.create_user(f"judge{number}", password="password123")
        member(judge, event, EventMembership.Role.JUDGE)
        judges.append(judge)
    submissions = []
    for number in range(6):
        owner = User.objects.create_user(f"owner{number}", password="password123")
        member(owner, event, EventMembership.Role.PARTICIPANT)
        submissions.append(submission(event, owner, track_a if number % 2 else track_b, f"Project {number}"))
    return organizer, event, track_a, track_b, judges, submissions, criterion


def test_assignment_count_balance_no_self_assignment_no_duplicates_and_idempotence(assignment_context):
    _, event, _, _, judges, submissions, _ = assignment_context
    # Make one judge a member of a project team; there are still four eligible
    # judges, enough for the configured three reviews.
    TeamMembership.objects.create(team=submissions[0].team, user=judges[0])

    first = generate_assignments(event)
    assert first["created"] == len(submissions) * event.reviews_per_submission
    assert all(
        JudgeAssignment.objects.filter(event=event, submission=project).count() == event.reviews_per_submission
        for project in submissions
    )
    assert not JudgeAssignment.objects.filter(submission=submissions[0], judge=judges[0]).exists()
    assert not any(
        TeamMembership.objects.filter(team=assignment.submission.team, user=assignment.judge).exists()
        for assignment in JudgeAssignment.objects.filter(event=event).select_related("submission", "judge")
    )
    pairs = list(JudgeAssignment.objects.filter(event=event).values_list("judge_id", "submission_id"))
    assert len(pairs) == len(set(pairs))
    loads = [JudgeAssignment.objects.filter(event=event, judge=judge).count() for judge in judges]
    assert max(loads) - min(loads) <= 1

    before = sorted(pairs)
    second = generate_assignments(event)
    after = sorted(JudgeAssignment.objects.filter(event=event).values_list("judge_id", "submission_id"))
    assert second["created"] == 0
    assert before == after


def test_assignment_respects_explicit_track_scope(t2_context):
    organizer, event, track_a, track_b, _, _ = t2_context
    event.reviews_per_submission = 1
    event.save(update_fields=["reviews_per_submission"])
    judge_a = User.objects.create_user("track_judge_a", password="password123")
    judge_b = User.objects.create_user("track_judge_b", password="password123")
    member(judge_a, event, EventMembership.Role.JUDGE)
    member(judge_b, event, EventMembership.Role.JUDGE)
    JudgeTrack.objects.create(event=event, judge=judge_a, track=track_a)
    JudgeTrack.objects.create(event=event, judge=judge_b, track=track_b)
    owner_a = User.objects.create_user("owner_track_a", password="password123")
    owner_b = User.objects.create_user("owner_track_b", password="password123")
    member(owner_a, event, EventMembership.Role.PARTICIPANT)
    member(owner_b, event, EventMembership.Role.PARTICIPANT)
    project_a = submission(event, owner_a, track_a, "Scoped A")
    project_b = submission(event, owner_b, track_b, "Scoped B")

    generate_assignments(event)
    assert JudgeAssignment.objects.get(submission=project_a).judge == judge_a
    assert JudgeAssignment.objects.get(submission=project_b).judge == judge_b


@pytest.fixture
def score_context(t2_context):
    organizer, event, track_a, track_b, rubric, criterion = t2_context
    owner = User.objects.create_user("score_owner", password="password123")
    judge = User.objects.create_user("score_judge", password="password123")
    peer = User.objects.create_user("score_peer", password="password123")
    participant = User.objects.create_user("score_participant", password="password123")
    for user, role in ((owner, EventMembership.Role.PARTICIPANT), (judge, EventMembership.Role.JUDGE), (peer, EventMembership.Role.JUDGE), (participant, EventMembership.Role.PARTICIPANT)):
        member(user, event, role)
    project = submission(event, owner, track_a, "Score Project")
    assignment = JudgeAssignment.objects.create(event=event, judge=judge, submission=project)
    return organizer, event, track_a, track_b, criterion, judge, peer, participant, assignment


def test_scoring_api_enforces_ownership_validation_and_audit(score_context):
    organizer, event, _, _, criterion, judge, peer, participant, assignment = score_context
    client = api_for(judge)
    created = client.post(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": criterion.pk, "value": 4}, format="json"
    )
    assert created.status_code == 201
    assert AuditLog.objects.filter(actor=judge, action="score.created", target_id=str(created.data["id"])).exists()
    updated = client.patch(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": criterion.pk, "value": 5}, format="json"
    )
    assert updated.status_code == 200
    assert AuditLog.objects.filter(actor=judge, action="score.updated").exists()
    assert JudgeAssignment.objects.get(pk=assignment.pk).status == JudgeAssignment.Status.COMPLETED
    assert api_for(peer).post(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": criterion.pk, "value": 3}, format="json"
    ).status_code == 403
    assert api_for(participant).post(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": criterion.pk, "value": 3}, format="json"
    ).status_code == 403
    assert client.post(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": criterion.pk, "value": 6}, format="json"
    ).status_code == 400

    other_event = Event.objects.create(name="Other", slug="other", created_by=organizer)
    other_rubric = Rubric.objects.create(event=other_event, name="Other rubric")
    other_criterion = RubricCriterion.objects.create(rubric=other_rubric, name="Other criterion", max_score=5)
    assert client.post(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": other_criterion.pk, "value": 3}, format="json"
    ).status_code == 400


def test_score_read_role_and_track_isolation(score_context):
    organizer, event, _, track_b, criterion, judge, peer, participant, assignment = score_context
    Score.objects.create(assignment=assignment, criterion=criterion, value=4)
    assert api_for(judge).get("/api/judge/scores").status_code == 200
    assert api_for(judge).get(f"/api/judge/scores?judge={peer.username}").status_code == 403
    assert api_for(participant).get("/api/judge/scores").status_code == 403
    organizer_response = api_for(organizer).get(f"/api/judge/scores?judge={judge.username}")
    assert organizer_response.status_code == 200
    assert organizer_response.data["scores"][0]["judge"] == judge.username
    assert api_for(organizer).post(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": criterion.pk, "value": 2}, format="json"
    ).status_code == 403

    JudgeTrack.objects.create(event=event, judge=judge, track=track_b)
    assert api_for(judge).post(
        "/api/judge/scores", {"assignment_id": assignment.pk, "criterion_id": criterion.pk, "value": 2}, format="json"
    ).status_code == 403


def test_progress_api_and_htmx_dashboard_are_organizer_only(score_context, client):
    organizer, event, _, _, criterion, judge, _, participant, assignment = score_context
    assignment.status = JudgeAssignment.Status.COMPLETED
    assignment.save(update_fields=["status"])
    owner = User.objects.create_user("progress_owner", password="password123")
    member(owner, event, EventMembership.Role.PARTICIPANT)
    other_project = submission(event, owner, assignment.submission.track, "Progress Other")
    JudgeAssignment.objects.create(event=event, judge=judge, submission=other_project)

    response = api_for(organizer).get("/api/judging/progress")
    assert response.status_code == 200
    item = next(row for row in response.data["judges"] if row["judge"] == judge.username)
    assert item == {"judge_id": judge.pk, "judge": judge.username, "assigned": 2, "completed": 1, "remaining": 1, "completion_percentage": 50.0}
    assert api_for(participant).get("/api/judging/progress").status_code == 403

    client.login(username=organizer.username, password="password123")
    page = client.get(f"/events/{event.slug}/judging/progress/")
    assert page.status_code == 200
    assert 'hx-trigger="load, every 10s"' in page.content.decode()
    partial = client.get(f"/events/{event.slug}/judging/progress/", HTTP_HX_REQUEST="true")
    assert partial.status_code == 200
    client.logout()
    client.login(username=participant.username, password="password123")
    assert client.get(f"/events/{event.slug}/judging/progress/").status_code == 403


def test_normalization_handles_weights_incomplete_reviews_and_zero_variance(t2_context):
    organizer, event, track_a, _, rubric, quality = t2_context
    impact = RubricCriterion.objects.create(rubric=rubric, name="Impact", weight=3, max_score=10)
    judge_variable = User.objects.create_user("variable", password="password123")
    judge_constant = User.objects.create_user("constant", password="password123")
    for judge in (judge_variable, judge_constant):
        member(judge, event, EventMembership.Role.JUDGE)
    projects = []
    for number in range(4):
        owner = User.objects.create_user(f"normal_owner{number}", password="password123")
        member(owner, event, EventMembership.Role.PARTICIPANT)
        projects.append(submission(event, owner, track_a, f"Normal {number}"))
    high = JudgeAssignment.objects.create(event=event, judge=judge_variable, submission=projects[0])
    low = JudgeAssignment.objects.create(event=event, judge=judge_variable, submission=projects[1])
    partial = JudgeAssignment.objects.create(event=event, judge=judge_constant, submission=projects[2])
    empty = JudgeAssignment.objects.create(event=event, judge=judge_constant, submission=projects[3])
    Score.objects.bulk_create([
        Score(assignment=high, criterion=quality, value=5), Score(assignment=high, criterion=impact, value=10),
        Score(assignment=low, criterion=quality, value=1), Score(assignment=low, criterion=impact, value=2),
        Score(assignment=partial, criterion=quality, value=5),
    ])

    run = run_normalization(event)
    assert isinstance(run, NormalizationRun)
    records = {record["assignment_id"]: record for record in run.snapshot["assignments"]}
    assert records[high.pk]["raw_weighted_score"] == pytest.approx(1.0)
    assert records[low.pk]["raw_weighted_score"] == pytest.approx(0.2)
    assert records[high.pk]["normalized_score"] == pytest.approx(1.0)
    assert records[low.pk]["normalized_score"] == pytest.approx(-1.0)
    assert records[partial.pk]["raw_weighted_score"] == pytest.approx(1.0)
    assert records[partial.pk]["normalized_score"] == 0.0
    assert records[empty.pk]["included"] is False
    assert run.snapshot["judge_statistics"][str(judge_constant.pk)]["fallback"] == "zero_variance"
    assert all(
        math.isfinite(record["normalized_score"])
        for record in records.values() if record["included"]
    )
    persisted = NormalizationRun.objects.get(pk=run.pk)
    assert persisted.snapshot["formula"]["zero_variance"].endswith("population_stddev = 0")
    assert persisted.snapshot["projects"][str(projects[0].pk)]["normalized_mean"] == pytest.approx(1.0)


def test_normalization_endpoint_and_csv_export_include_scores_and_progress(score_context):
    organizer, event, _, _, criterion, judge, _, participant, assignment = score_context
    Score.objects.create(assignment=assignment, criterion=criterion, value=4)
    assignment.status = JudgeAssignment.Status.COMPLETED
    assignment.save(update_fields=["status"])
    normalize = api_for(organizer).post("/api/judging/normalization/run", {"event": event.slug}, format="json")
    assert normalize.status_code == 201
    assert NormalizationRun.objects.filter(event=event).exists()
    response = api_for(organizer).get("/api/export.csv")
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode())))
    assert "raw_weighted_score" in response.content.decode().splitlines()[0]
    assert "judge_completion_percentage" in response.content.decode().splitlines()[0]
    assert any(row["judge"] == judge.username for row in rows)
    assert api_for(participant).get("/api/export.csv").status_code == 403
