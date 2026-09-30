"""Event and project card content, and fixed query counts for both list pages."""
import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.core.templatetags.ui import EVENT_GLYPHS, event_glyph
from apps.events.models import Event, Prize, Track
from apps.submissions.models import Submission
from apps.teams.models import Team


def make_event(slug, *, tracks=0, prizes=0, teams=0, submitted=0, drafts=0, description=""):
    owner = User.objects.create_user(f"{slug}_owner", password="password123")
    event = Event.objects.create(name=slug.title(), slug=slug, created_by=owner, is_published=True, description=description)
    created_tracks = [Track.objects.create(event=event, name=f"Track {chr(70 - n)}") for n in range(tracks)]
    for n in range(prizes):
        Prize.objects.create(event=event, name=f"Prize {n}")
    team_list = [Team.objects.create(event=event, name=f"{slug} team {n}", created_by=owner) for n in range(max(teams, 1))]
    for n in range(submitted + drafts):
        Submission.objects.create(
            event=event, team=team_list[0], title=f"{slug} project {n}",
            track=created_tracks[0] if created_tracks else None,
            status=Submission.Status.SUBMITTED if n < submitted else Submission.Status.DRAFT,
        )
    return event


def count_queries(url):
    with CaptureQueriesContext(connection) as queries:
        response = Client().get(url)
    assert response.status_code == 200
    return len(queries), response


@pytest.mark.django_db
def test_events_page_counts_chips_and_description():
    make_event("rich", tracks=6, prizes=2, teams=3, submitted=2, drafts=1, description="A long description.")
    make_event("plain")
    _, response = count_queries("/events/")
    cards = {event.slug: event for event in response.context["events"]}

    rich = cards["rich"]
    assert (rich.project_count, rich.track_count, rich.prize_count, rich.team_count) == (2, 6, 2, 3)
    assert rich.track_chips == ["Track A", "Track B", "Track C", "Track D"]  # first four by name
    assert rich.more_tracks == 2
    assert (cards["plain"].track_count, cards["plain"].prize_count, cards["plain"].more_tracks) == (0, 0, 0)

    content = response.content.decode()
    assert content.count('class="event-card-desc clamp-3"') == 1  # hidden for the empty description
    assert "+2" in content and "Dates to be announced" in content


@pytest.mark.django_db
def test_events_page_runs_a_fixed_number_of_queries():
    make_event("first", tracks=2, prizes=1, teams=2, submitted=1)
    one, _ = count_queries("/events/")
    for n in range(4):
        make_event(f"more{n}", tracks=5, prizes=3, teams=4, submitted=3)
    many, _ = count_queries("/events/")
    assert one == many == 2
    assert count_queries("/events/?status=past")[0] == 2


def test_event_glyph_is_deterministic_and_from_the_known_set():
    assert event_glyph("evt_01") == event_glyph("evt_01")
    assert {event_glyph(f"event-{n}") for n in range(60)} <= set(EVENT_GLYPHS)
    assert len({event_glyph(f"event-{n}") for n in range(60)}) > 1


@pytest.mark.django_db
def test_gallery_cards_show_real_text_and_only_existing_links():
    event = make_event("gallery")
    team = Team.objects.get(event=event)
    both = dict(event=event, team=team, status=Submission.Status.SUBMITTED)
    Submission.objects.create(title="Tagline Wins", tagline="The tagline", summary="The summary", repo_url="https://example.org/r", **both)
    Submission.objects.create(title="Summary Only", summary="Only a summary", live_url="https://example.org/l", **both)
    Submission.objects.create(title="No Text", demo_video_url="https://example.org/v", **both)
    Submission.objects.create(title="No Links", summary="Quiet", **both)

    queries, response = count_queries("/projects/")
    content = response.content.decode()
    # Cards show only the tagline; summaries stay on the project page.
    assert "The tagline" in content
    assert "The summary" not in content and "Only a summary" not in content and "Quiet" not in content
    assert content.count('class="project-card-summary clamp-2"') == 1
    summary_only = Submission.objects.get(title="Summary Only")
    assert "Only a summary" in Client().get(f"/projects/{summary_only.pk}/").content.decode()
    assert content.count('<ul class="link-indicators"') == 3  # "No Links" has none
    assert content.count(" Repo</li>") == 1
    assert content.count(" Live demo</li>") == 1
    assert content.count(" Video</li>") == 1
    lowered = content.lower()
    assert "vote" not in lowered and "score" not in lowered

    for n in range(6):
        Submission.objects.create(title=f"Extra {n}", summary="x", repo_url="https://example.org/x", **both)
    assert count_queries("/projects/")[0] == queries
