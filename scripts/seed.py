#!/usr/bin/env python
"""
Seed script for Dogfood Portal.

Loads the real fixtures.json and creates all required database records:
- Event with real submissions_close timestamp
- Tracks
- Judge accounts (with DRF tokens)
- Teams and team members
- Submitted projects
- Scores

Prints ready-to-paste .dogfood.toml [auth] lines to stdout.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils.dateparse import parse_datetime
from rest_framework.authtoken.models import Token

from apps.events.models import Event, Track
from apps.teams.models import Team, TeamMembership
from apps.submissions.models import Submission
from apps.accounts.models import EventMembership
from apps.judging.models import (
    Rubric, RubricCriterion, JudgeAssignment, Score,
)

User = get_user_model()

DEFAULT_PASSWORD = "dogfood2026"
DEFAULT_PASSWORD_HASH = make_password(DEFAULT_PASSWORD)


def load_fixtures():
    """Load the official fixtures.json file."""
    fixtures_path = BASE_DIR / "fixtures" / "fixtures.json"
    if not fixtures_path.exists():
        print(f"ERROR: {fixtures_path} not found!")
        sys.exit(1)
    with open(fixtures_path, "r") as f:
        return json.load(f)


def create_or_get_user(username, email="", display_name=""):
    """Create a user if it doesn't exist, return user."""
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": email or f"{username}@example.org",
            "display_name": display_name or username,
            "password": DEFAULT_PASSWORD_HASH,
        }
    )
    return user


def get_or_create_token(user):
    """Get or create a DRF auth token for a user."""
    token, _ = Token.objects.get_or_create(user=user)
    return token.key


def ensure_membership(user, event, role):
    """Keep the seed accounts active with their documented event role."""
    membership, _ = EventMembership.objects.update_or_create(
        user=user,
        event=event,
        defaults={
            "role": role,
            "status": EventMembership.Status.ACTIVE,
        },
    )
    return membership


@transaction.atomic
def seed():
    """Main seed function."""
    data = load_fixtures()

    print("=" * 60)
    print("DOGFOOD PORTAL — Seeding from fixtures.json")
    print("=" * 60)

    # 1. Create the named acceptance accounts before assigning the event owner.
    organizer = create_or_get_user("organizer", "organizer@example.org", "Organizer")
    participant = create_or_get_user("participant", "participant@example.org", "Participant")

    # 2. Synchronize the event, including the fixture's real closed deadline.
    event_data = data["event"]
    event, created = Event.objects.update_or_create(
        slug=event_data["id"],
        defaults={
            "name": event_data["name"],
            "submission_deadline": parse_datetime(event_data["submissions_close"]),
            "is_published": True,
            "created_by": organizer,
        }
    )
    if created:
        print(f"[ok] Event: {event.name} (deadline: {event.submission_deadline})")
    else:
        print(f"  Event already exists: {event.name}")

    ensure_membership(organizer, event, EventMembership.Role.ORGANIZER)
    ensure_membership(participant, event, EventMembership.Role.PARTICIPANT)

    # 3. Create Tracks
    track_map = {}  # fixture_id -> Track object
    for t in data["tracks"]:
        track, _ = Track.objects.get_or_create(
            event=event,
            name=t["name"],
        )
        track_map[t["id"]] = track
    print(f"[ok] Tracks: {len(track_map)} created/found")

    # 4. Create judge accounts
    judge_map = {}  # fixture_id -> User object
    for i, j in enumerate(data["judges"]):
        judge_user = create_or_get_user(
            username=j["email"].split("@")[0].replace(".", "_"),
            email=j["email"],
            display_name=j["name"],
        )
        judge_map[j["id"]] = judge_user
        ensure_membership(judge_user, event, EventMembership.Role.JUDGE)
    print(f"[ok] Judges: {len(judge_map)} created/found")

    # Create the two required named judge accounts: judge_a and judge_b
    # Map to the first two judges in the fixture
    # The acceptance runner uses these two named accounts.
    judge_a_user = create_or_get_user("judge_a", "judge_a@example.org", "Judge A")
    ensure_membership(judge_a_user, event, EventMembership.Role.JUDGE)

    judge_b_user = create_or_get_user("judge_b", "judge_b@example.org", "Judge B")
    ensure_membership(judge_b_user, event, EventMembership.Role.JUDGE)

    # 5. Create team members and teams
    team_map = {}  # fixture_id -> Team object
    for t in data["teams"]:
        team, _ = Team.objects.update_or_create(
            event=event,
            fixture_id=t["id"],
            defaults={
                "name": t["name"],
                "created_by": organizer,
            },
        )
        team_map[t["id"]] = team

        for email in t["members"]:
            username = email.split("@")[0].replace(".", "_")
            member_user = create_or_get_user(username, email, username)
            TeamMembership.objects.get_or_create(team=team, user=member_user)
            ensure_membership(member_user, event, EventMembership.Role.PARTICIPANT)
    print(f"[ok] Teams: {len(team_map)} created/found")

    # Give participant a team too (for the submit test)
    part_team, _ = Team.objects.get_or_create(
        event=event,
        name="Test Team",
        defaults={"created_by": participant},
    )
    TeamMembership.objects.get_or_create(team=part_team, user=participant)

    # 6. Create submissions (all as 'submitted' status).
    # fixture_id prevents the official prj_07/prj_41 duplicate title from
    # collapsing into one row and preserves direct score-to-project mapping.
    submission_map = {}
    for p in data["projects"]:
        team = team_map.get(p["team"])
        track = track_map.get(p["track"])
        if not team:
            continue

        sub, _ = Submission.objects.update_or_create(
            event=event,
            fixture_id=p["id"],
            defaults={
                "team": team,
                "track": track,
                "title": p["title"],
                "summary": p.get("summary", ""),
                "repo_url": p.get("repo_url", ""),
                "status": Submission.Status.SUBMITTED,
                "submitted_at": parse_datetime(p["submitted_at"]),
            }
        )
        submission_map[p["id"]] = sub
    print(f"[ok] Projects: {len(submission_map)} fixture projects synchronized")

    # 7. Create rubric and criteria for scores
    rubric, _ = Rubric.objects.get_or_create(
        event=event,
        name="Default Rubric",
        defaults={"is_active": True},
    )
    criteria_names = set()
    for s in data["scores"]:
        for crit_name in s["criteria"].keys():
            criteria_names.add(crit_name)

    criteria_map = {}
    for crit_name in criteria_names:
        criterion, _ = RubricCriterion.objects.get_or_create(
            rubric=rubric,
            name=crit_name,
            defaults={"weight": 1.0, "max_score": 5},
        )
        criteria_map[crit_name] = criterion

    # 8. Create scores from fixture data
    for s in data["scores"]:
        judge_user = judge_map.get(s["judge"])
        if not judge_user:
            continue

        submission = submission_map.get(s["project"])

        if not submission:
            continue

        assignment, _ = JudgeAssignment.objects.update_or_create(
            event=event,
            judge=judge_user,
            submission=submission,
            defaults={"status": JudgeAssignment.Status.COMPLETED},
        )

        for crit_name, value in s["criteria"].items():
            criterion = criteria_map.get(crit_name)
            if criterion:
                Score.objects.update_or_create(
                    assignment=assignment,
                    criterion=criterion,
                    defaults={"value": value},
                )

    print(f"[ok] Scores: {Score.objects.filter(assignment__event=event).count()} criterion values synchronized")

    # 9. Generate tokens and print .dogfood.toml auth lines
    org_token = get_or_create_token(organizer)
    judge_a_token = get_or_create_token(judge_a_user)
    judge_b_token = get_or_create_token(judge_b_user)
    part_token = get_or_create_token(participant)

    print()
    print("=" * 60)
    print("SEEDED. Test logins (paste into .dogfood.toml [auth]):")
    print("=" * 60)
    print(f'organizer   = "Authorization: Token {org_token}"')
    print(f'judge_a     = "Authorization: Token {judge_a_token}"')
    print(f'judge_b     = "Authorization: Token {judge_b_token}"')
    print(f'participant = "Authorization: Token {part_token}"')
    print()
    print("Default password for all accounts:", DEFAULT_PASSWORD)
    print("=" * 60)


if __name__ == "__main__":
    seed()
