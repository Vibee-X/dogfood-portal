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
from collections import Counter
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
    JudgeAssignment, JudgeTrack, NormalizationRun, Rubric, RubricCriterion, Score,
)
from apps.judging.services import judge_can_review_submission, run_normalization

User = get_user_model()

DEFAULT_PASSWORD = "dogfood2026"
DEMO_BATCH_ID = "seed-demo-judges"
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

    # Fixture track scopes are authoritative. Named acceptance judges have no
    # scope rows and therefore remain event-wide judges until an organizer
    # invites them with explicit tracks.
    JudgeTrack.objects.filter(event=event, judge__in=judge_map.values()).delete()
    JudgeTrack.objects.bulk_create([
        JudgeTrack(event=event, judge=judge_map[judge_id], track=track_map[track_id])
        for judge_id, judge_data in ((judge["id"], judge) for judge in data["judges"])
        for track_id in judge_data.get("tracks", [])
        if track_id in track_map
    ])

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

    if not NormalizationRun.objects.filter(event=event).exists():
        run_normalization(event)
        print("[ok] Normalization: weighted z-score snapshot created")

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


@transaction.atomic
def seed_demo_assignments():
    """Give the named demo judges real, unscored work (idempotent).

    Kept separate from seed(), which stays a faithful import of the fixture.
    The fixture's unfinished batches leave some projects below the event's
    reviews_per_submission. Each such project gets the missing reviews as
    pending assignments for judge_a and judge_b, alternating in fixture-id
    order, so the judge scoring page has work on a fresh install. Selection
    comes from the fixture file, so repeated runs pick the same projects, and
    get_or_create on (judge, submission) never duplicates a row. Every
    assignment passes the app's own rules (judge_can_review_submission and
    JudgeAssignment.full_clean) before it is written. This runs after the
    normalization snapshot, so the stored fixture results are unchanged.
    """
    data = load_fixtures()
    event = Event.objects.get(slug=data["event"]["id"])
    judges = [User.objects.get(username="judge_a"), User.objects.get(username="judge_b")]
    submissions = {
        s.fixture_id: s
        for s in Submission.objects.filter(event=event, fixture_id__isnull=False).select_related("team")
    }
    fixture_judges = {j["id"] for j in data["judges"]}
    reviews = Counter(
        s["project"] for s in data["scores"] if s["project"] in submissions and s["judge"] in fixture_judges
    )
    target = event.reviews_per_submission
    under_target = sorted(fid for fid in submissions if reviews[fid] < target)

    created, existing, placed = 0, 0, []
    for index, fixture_id in enumerate(under_target):
        submission = submissions[fixture_id]
        for offset in range(min(target - reviews[fixture_id], len(judges))):
            judge = judges[(index + offset) % len(judges)]
            if not judge_can_review_submission(judge, event, submission):
                raise ValueError(f"{judge.username} is not eligible to review {fixture_id}")
            JudgeAssignment(event=event, judge=judge, submission=submission, batch_id=DEMO_BATCH_ID).full_clean(
                validate_unique=False, validate_constraints=False,
            )
            _, was_created = JudgeAssignment.objects.get_or_create(
                judge=judge,
                submission=submission,
                defaults={"event": event, "batch_id": DEMO_BATCH_ID},
            )
            created += was_created
            existing += not was_created
            placed.append(f"{fixture_id}->{judge.username}")
    print(
        f"[ok] Demo judge assignments: {created} created, {existing} already present "
        f"({', '.join(placed) or 'no project below target'})"
    )


if __name__ == "__main__":
    seed()
    seed_demo_assignments()
