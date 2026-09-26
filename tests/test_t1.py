"""
Dogfood Portal — T1 Test Suite

Tests cover:
- Auth flow (signup, login, logout)
- Deadline enforcement (server-side)
- Invite-link team joining
- Gallery search and filtering
- Role isolation (participant blocked from organizer endpoints)
- Public gallery access
- Submission API with Token auth
"""
import pytest
from datetime import datetime, timedelta, timezone as datetime_timezone
from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token

from apps.accounts.models import User, EventMembership
from apps.events.models import Event, Track
from apps.teams.models import Team, TeamMembership, InviteLink
from apps.submissions.models import Submission


@pytest.fixture
def user_data():
    return {
        "username": "testuser",
        "email": "test@example.org",
        "password": "testpass123!",
    }


@pytest.fixture
def create_user(db):
    def _create(username="testuser", email="test@example.org", password="testpass123!"):
        user = User.objects.create_user(
            username=username, email=email, password=password,
            display_name=username
        )
        return user
    return _create


@pytest.fixture
def event_with_past_deadline(db, create_user):
    """Event whose submission deadline has already passed — mirrors the fixture event."""
    organizer = create_user("organizer", "org@example.org")
    event = Event.objects.create(
        name="Test Hack 2026",
        slug="test-hack-2026",
        submission_deadline=datetime(2026, 3, 1, 18, 0, 0, tzinfo=datetime_timezone.utc),
        is_published=True,
        created_by=organizer,
    )
    EventMembership.objects.create(
        user=organizer, event=event, role=EventMembership.Role.ORGANIZER
    )
    return event, organizer


@pytest.fixture
def event_with_future_deadline(db, create_user):
    """Event whose submission deadline is in the future."""
    organizer = create_user("org_future", "orgf@example.org")
    event = Event.objects.create(
        name="Future Hack",
        slug="future-hack",
        submission_deadline=timezone.now() + timedelta(days=30),
        is_published=True,
        created_by=organizer,
    )
    EventMembership.objects.create(
        user=organizer, event=event, role=EventMembership.Role.ORGANIZER
    )
    return event, organizer


# ============================================================
# Auth Tests
# ============================================================

@pytest.mark.django_db
class TestAuth:
    def test_signup(self, client):
        resp = client.post(reverse("accounts:signup"), {
            "username": "newuser",
            "email": "new@example.org",
            "password1": "complexpass123!",
            "password2": "complexpass123!",
        })
        assert resp.status_code in (200, 302)
        assert User.objects.filter(username="newuser").exists()

    def test_login(self, client, create_user):
        create_user("loginuser", "login@example.org", "testpass123!")
        resp = client.post(reverse("accounts:login"), {
            "username": "loginuser",
            "password": "testpass123!",
        })
        assert resp.status_code == 302  # redirect on success

    def test_login_fail(self, client, create_user):
        create_user("loginuser2", "login2@example.org", "testpass123!")
        resp = client.post(reverse("accounts:login"), {
            "username": "loginuser2",
            "password": "wrongpassword",
        })
        assert resp.status_code == 200  # stays on login page

    def test_logout(self, client, create_user):
        user = create_user("logoutuser", "logout@example.org", "testpass123!")
        client.login(username="logoutuser", password="testpass123!")
        resp = client.get(reverse("accounts:logout"))
        assert resp.status_code == 302


# ============================================================
# Deadline Enforcement Tests
# ============================================================

@pytest.mark.django_db
class TestDeadlineEnforcement:
    def test_api_rejects_late_submission(self, event_with_past_deadline, create_user):
        """Server-side deadline blocks POST /projects/new after deadline."""
        event, organizer = event_with_past_deadline
        participant = create_user("late_submitter", "late@example.org")
        EventMembership.objects.create(
            user=participant, event=event, role=EventMembership.Role.PARTICIPANT
        )
        team = Team.objects.create(event=event, name="Late Team", created_by=participant)
        TeamMembership.objects.create(team=team, user=participant)

        token = Token.objects.create(user=participant)
        api_client = APIClient()
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        resp = api_client.post(
            "/projects/new",
            {"title": "dogfood-late-submission-probe", "summary": "probe"},
            format="json",
        )
        assert 400 <= resp.status_code < 500, f"Expected 4xx, got {resp.status_code}"

    def test_api_accepts_submission_before_deadline(self, event_with_future_deadline, create_user):
        """Submission succeeds before deadline."""
        event, organizer = event_with_future_deadline
        participant = create_user("early_submitter", "early@example.org")
        EventMembership.objects.create(
            user=participant, event=event, role=EventMembership.Role.PARTICIPANT
        )
        team = Team.objects.create(event=event, name="Early Team", created_by=participant)
        TeamMembership.objects.create(team=team, user=participant)

        token = Token.objects.create(user=participant)
        api_client = APIClient()
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        resp = api_client.post(
            "/projects/new",
            {"title": "My Awesome Project", "summary": "Does cool things"},
            format="json",
        )
        assert resp.status_code == 201


# ============================================================
# Invite Link Tests
# ============================================================

@pytest.mark.django_db
class TestInviteLink:
    def test_join_via_invite_link(self, event_with_past_deadline, create_user):
        event, organizer = event_with_past_deadline
        creator = create_user("team_creator", "creator@example.org")
        team = Team.objects.create(event=event, name="Invite Team", created_by=creator)
        TeamMembership.objects.create(team=team, user=creator)

        invite = InviteLink.objects.create(team=team, code="test-invite-code")

        joiner = create_user("joiner", "joiner@example.org")
        client = Client()
        client.login(username="joiner", password="testpass123!")
        resp = client.get(reverse("teams:join_team", kwargs={"code": "test-invite-code"}))
        assert resp.status_code == 302
        assert TeamMembership.objects.filter(team=team, user=joiner).exists()

    def test_expired_invite_link(self, event_with_past_deadline, create_user):
        event, organizer = event_with_past_deadline
        creator = create_user("team_creator2", "creator2@example.org")
        team = Team.objects.create(event=event, name="Expired Team", created_by=creator)
        TeamMembership.objects.create(team=team, user=creator)

        invite = InviteLink.objects.create(
            team=team, code="expired-invite",
            expires_at=timezone.now() - timedelta(hours=1)
        )

        joiner = create_user("joiner2", "joiner2@example.org")
        client = Client()
        client.login(username="joiner2", password="testpass123!")
        resp = client.get(reverse("teams:join_team", kwargs={"code": "expired-invite"}))
        assert not TeamMembership.objects.filter(team=team, user=joiner).exists()


# ============================================================
# Gallery Tests
# ============================================================

@pytest.mark.django_db
class TestGallery:
    def test_public_gallery_accessible(self, client):
        """Gallery is accessible without authentication."""
        resp = client.get("/projects/")
        assert resp.status_code == 200

    def test_gallery_shows_only_submitted(self, event_with_past_deadline, create_user, client):
        """Gallery shows only submitted projects, not drafts."""
        event, organizer = event_with_past_deadline
        team = Team.objects.create(event=event, name="Gallery Team", created_by=organizer)

        # Create a draft
        Submission.objects.create(
            team=team, event=event, title="Draft Project",
            status=Submission.Status.DRAFT,
        )
        # Create a submitted project
        Submission.objects.create(
            team=team, event=event, title="Submitted Project",
            status=Submission.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )

        resp = client.get("/projects/")
        content = resp.content.decode()
        assert "Submitted Project" in content
        assert "Draft Project" not in content

    def test_gallery_search(self, event_with_past_deadline, create_user, client):
        """Gallery search by title."""
        event, organizer = event_with_past_deadline
        team = Team.objects.create(event=event, name="Search Team", created_by=organizer)
        Submission.objects.create(
            team=team, event=event, title="Unique Searchable Title",
            status=Submission.Status.SUBMITTED, submitted_at=timezone.now(),
        )

        resp = client.get("/projects/", {"q": "Unique Searchable"})
        assert "Unique Searchable Title" in resp.content.decode()

    def test_gallery_track_filter(self, event_with_past_deadline, create_user, client):
        """Gallery filter by track."""
        event, organizer = event_with_past_deadline
        track = Track.objects.create(event=event, name="Special Track")
        other_track = Track.objects.create(event=event, name="Other Track")
        team = Team.objects.create(event=event, name="Filter Team", created_by=organizer)

        Submission.objects.create(
            team=team, event=event, title="In Special Track",
            track=track, status=Submission.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        Submission.objects.create(
            team=team, event=event, title="In Other Track",
            track=other_track, status=Submission.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )

        resp = client.get("/projects/", {"track": track.pk})
        content = resp.content.decode()
        assert "In Special Track" in content
        assert "In Other Track" not in content


# ============================================================
# Role Isolation Tests
# ============================================================

@pytest.mark.django_db
class TestRoleIsolation:
    def test_participant_cannot_access_judge_scores(self, event_with_past_deadline, create_user):
        """Participant is blocked from /api/judge/scores."""
        event, organizer = event_with_past_deadline
        participant = create_user("participant_blocked", "pblocked@example.org")
        EventMembership.objects.create(
            user=participant, event=event, role=EventMembership.Role.PARTICIPANT
        )

        token = Token.objects.create(user=participant)
        api_client = APIClient()
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        resp = api_client.get("/api/judge/scores")
        assert resp.status_code == 403

    def test_judge_cannot_see_peer_scores(self, event_with_past_deadline, create_user):
        """Judge B cannot see Judge A's scores."""
        event, organizer = event_with_past_deadline

        judge_a = create_user("judge_a_test", "ja@example.org")
        EventMembership.objects.create(
            user=judge_a, event=event, role=EventMembership.Role.JUDGE
        )

        judge_b = create_user("judge_b_test", "jb@example.org")
        EventMembership.objects.create(
            user=judge_b, event=event, role=EventMembership.Role.JUDGE
        )

        token_b = Token.objects.create(user=judge_b)
        api_client = APIClient()
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token_b.key}")

        resp = api_client.get("/api/judge/scores?judge=judge_a_test")
        assert resp.status_code == 403

    def test_judge_can_see_own_scores(self, event_with_past_deadline, create_user):
        """Judge A can see their own scores."""
        event, organizer = event_with_past_deadline

        judge_a = create_user("judge_a_own", "ja_own@example.org")
        EventMembership.objects.create(
            user=judge_a, event=event, role=EventMembership.Role.JUDGE
        )

        token_a = Token.objects.create(user=judge_a)
        api_client = APIClient()
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token_a.key}")

        resp = api_client.get("/api/judge/scores")
        assert resp.status_code == 200

    def test_organizer_can_export_csv(self, event_with_past_deadline, create_user):
        """Organizer can access CSV export."""
        event, organizer = event_with_past_deadline
        token = Token.objects.create(user=organizer)
        api_client = APIClient()
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        resp = api_client.get("/api/export.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp["Content-Type"]

    def test_participant_cannot_export_csv(self, event_with_past_deadline, create_user):
        """Participant is blocked from CSV export."""
        event, organizer = event_with_past_deadline
        participant = create_user("part_csv", "pccsv@example.org")
        EventMembership.objects.create(
            user=participant, event=event, role=EventMembership.Role.PARTICIPANT
        )

        token = Token.objects.create(user=participant)
        api_client = APIClient()
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        resp = api_client.get("/api/export.csv")
        assert resp.status_code == 403

    def test_event_edit_only_organizer(self, event_with_past_deadline, create_user):
        """Non-organizer cannot edit events."""
        event, organizer = event_with_past_deadline
        participant = create_user("part_noedit", "pnoedit@example.org")

        client = Client()
        client.login(username="part_noedit", password="testpass123!")
        resp = client.post(reverse("events:event_edit", kwargs={"slug": event.slug}), {
            "name": "Hacked Event Name",
        })
        assert resp.status_code == 403


# ============================================================
# Project Detail Tests
# ============================================================

@pytest.mark.django_db
class TestProjectDetail:
    def test_submitted_project_accessible(self, event_with_past_deadline, client):
        event, organizer = event_with_past_deadline
        team = Team.objects.create(event=event, name="Detail Team", created_by=organizer)
        sub = Submission.objects.create(
            team=team, event=event, title="Detail Project",
            summary="Test summary",
            status=Submission.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        resp = client.get(reverse("submissions:project_detail", kwargs={"pk": sub.pk}))
        assert resp.status_code == 200
        assert "Detail Project" in resp.content.decode()

    def test_draft_project_not_accessible(self, event_with_past_deadline, client):
        event, organizer = event_with_past_deadline
        team = Team.objects.create(event=event, name="Draft Team", created_by=organizer)
        sub = Submission.objects.create(
            team=team, event=event, title="Secret Draft",
            status=Submission.Status.DRAFT,
        )
        resp = client.get(reverse("submissions:project_detail", kwargs={"pk": sub.pk}))
        assert resp.status_code == 404
