"""Who may host (create) events: staff, or active organizers/admins of an event."""
import pytest
from django.test import Client

from apps.accounts.models import EventMembership, User
from apps.events.models import Event
from scripts.seed import seed

NEW_EVENT = {
    "name": "New Jam",
    "slug": "new-jam",
    "voting_access": Event.VotingAccess.PARTICIPANTS,
    "reviews_per_submission": 3,
}


def web_for(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def seeded(db, capsys):
    seed()
    capsys.readouterr()  # the seed prints demo tokens; keep them out of test output


def test_participant_cannot_host_and_sees_no_button(seeded):
    web = web_for(User.objects.get(username="participant"))
    assert web.get("/events/create/").status_code == 403
    assert web.post("/events/create/", NEW_EVENT).status_code == 403
    assert not Event.objects.filter(slug=NEW_EVENT["slug"]).exists()
    assert "Host an event" not in web.get("/events/").content.decode()


def test_anonymous_visitors_are_sent_to_login(db):
    response = Client().get("/events/create/")
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
    assert "Host an event" not in Client().get("/events/").content.decode()


def test_seeded_organizer_can_host_and_becomes_its_organizer(seeded):
    organizer = User.objects.get(username="organizer")
    web = web_for(organizer)
    assert "Host an event" in web.get("/events/").content.decode()
    form = web.get("/events/create/")
    assert form.status_code == 200
    assert "It doesn't change your role in any other event." in form.content.decode()
    created = web.post("/events/create/", NEW_EVENT)
    assert created.status_code == 302
    hosted = Event.objects.get(slug=NEW_EVENT["slug"])
    assert EventMembership.objects.get(user=organizer, event=hosted).role == EventMembership.Role.ORGANIZER


def test_staff_user_can_host_but_gains_no_role_in_other_events(seeded):
    staff = User.objects.create_user("staff_host", password="password123", is_staff=True)
    web = web_for(staff)
    assert "Host an event" in web.get("/events/").content.decode()
    assert web.post("/events/create/", NEW_EVENT).status_code == 302
    hosted = Event.objects.get(slug=NEW_EVENT["slug"])
    assert EventMembership.objects.get(user=staff, event=hosted).role == EventMembership.Role.ORGANIZER
    assert web.get(f"/events/{hosted.slug}/audit/").status_code == 200
    # Being staff and hosting one event grants nothing in evt_01.
    assert web.get("/events/evt_01/audit/").status_code == 403
    assert not EventMembership.objects.filter(user=staff, event__slug="evt_01").exists()
