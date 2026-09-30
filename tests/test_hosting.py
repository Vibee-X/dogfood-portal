"""Hosting an event makes you its organizer and nothing more."""
import pytest
from django.test import Client
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import EventMembership, User
from apps.events.models import Event
from scripts.seed import seed


@pytest.mark.django_db
def test_participant_who_hosts_an_event_is_still_refused_in_evt_01(capsys):
    seed()
    capsys.readouterr()  # the seed prints demo tokens; keep them out of the test output
    participant = User.objects.get(username="participant")
    web = Client()
    web.force_login(participant)

    created = web.post("/events/create/", {
        "name": "Participant Jam",
        "slug": "participant-jam",
        "voting_access": Event.VotingAccess.PARTICIPANTS,
        "reviews_per_submission": 3,
    })
    assert created.status_code == 302
    hosted = Event.objects.get(slug="participant-jam")
    assert EventMembership.objects.get(user=participant, event=hosted).role == EventMembership.Role.ORGANIZER
    assert web.get(f"/events/{hosted.slug}/audit/").status_code == 200  # organizer of their own event
    # Their role in the seeded event is unchanged.
    assert EventMembership.objects.get(user=participant, event__slug="evt_01").role == EventMembership.Role.PARTICIPANT

    token, _ = Token.objects.get_or_create(user=participant)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    assert api.get("/api/judge/scores", {"event": "evt_01", "judge": "judge_a"}).status_code == 403
    assert api.get("/api/export.csv", {"event": "evt_01"}).status_code == 403
    assert web.get("/events/evt_01/audit/").status_code == 403


@pytest.mark.django_db
def test_events_page_and_form_explain_hosting():
    user = User.objects.create_user("host_candidate", password="password123")
    web = Client()
    web.force_login(user)
    assert "Host an event" in web.get("/events/").content.decode()
    assert "It doesn't change your role in any other event." in web.get("/events/create/").content.decode()
