import json
from pathlib import Path

import pytest
from django.utils.dateparse import parse_datetime
from rest_framework.authtoken.models import Token

from apps.accounts.models import EventMembership, User
from apps.events.models import Event, Track
from apps.judging.models import JudgeAssignment, Score
from apps.submissions.models import Submission
from apps.teams.models import Team, TeamMembership
from scripts.seed import seed


FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "fixtures.json"


@pytest.mark.django_db
def test_seed_preserves_official_fixture_and_acceptance_accounts(capsys):
    """The boot seed remains faithful to the official fixture and idempotent."""
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    seed()
    event = Event.objects.get(slug=fixture["event"]["id"])

    assert event.submission_deadline == parse_datetime(fixture["event"]["submissions_close"])
    assert Track.objects.filter(event=event).count() == len(fixture["tracks"])
    assert Team.objects.filter(event=event).count() == len(fixture["teams"]) + 1
    assert Submission.objects.filter(event=event).count() == len(fixture["projects"])
    assert set(Submission.objects.filter(event=event).values_list("fixture_id", flat=True)) == {
        project["id"] for project in fixture["projects"]
    }
    assert Submission.objects.filter(event=event, title="Dry Harbour").count() == 2
    assert JudgeAssignment.objects.filter(event=event).count() == len(fixture["scores"])
    assert Score.objects.filter(assignment__event=event).count() == sum(
        len(score["criteria"]) for score in fixture["scores"]
    )

    expected_roles = {
        "organizer": EventMembership.Role.ORGANIZER,
        "judge_a": EventMembership.Role.JUDGE,
        "judge_b": EventMembership.Role.JUDGE,
        "participant": EventMembership.Role.PARTICIPANT,
    }
    for username, role in expected_roles.items():
        user = User.objects.get(username=username)
        assert EventMembership.objects.get(user=user, event=event).role == role
        assert Token.objects.filter(user=user).exists()
        assert user.check_password("dogfood2026")

    assert TeamMembership.objects.filter(
        team__event=event,
        team__name="Test Team",
        user__username="participant",
    ).exists()

    seed()
    assert Submission.objects.filter(event=event).count() == len(fixture["projects"])
    assert JudgeAssignment.objects.filter(event=event).count() == len(fixture["scores"])
    assert Score.objects.filter(assignment__event=event).count() == sum(
        len(score["criteria"]) for score in fixture["scores"]
    )

    output = capsys.readouterr().out
    for username in expected_roles:
        assert f'{username}' in output
    assert "Authorization: Token" in output
