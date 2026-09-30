"""Initials on generated thumbnails."""
import pytest
from django.test import Client

from apps.accounts.models import User
from apps.core.templatetags.ui import initials
from apps.events.models import Event
from apps.submissions.models import Submission
from apps.teams.models import Team


def test_initials():
    assert initials("Dry Harbour") == "DH"
    assert initials("iron switch ledger") == "IS"
    assert initials("Zephyr") == "ZE"
    assert initials("  --  ") == ""
    assert initials(None) == ""
    assert initials("Élan vital") == "ÉV"


@pytest.mark.django_db
def test_gallery_and_embed_show_hidden_initials_without_new_links():
    owner = User.objects.create_user("thumb_owner", password="password123")
    event = Event.objects.create(name="Thumbs", slug="thumbs", created_by=owner, is_published=True)
    team = Team.objects.create(event=event, name="Thumb Team", created_by=owner)
    Submission.objects.create(event=event, team=team, title="Dry Harbour", status=Submission.Status.SUBMITTED)

    marker = '<span class="art-initials" aria-hidden="true">DH</span>'
    assert marker in Client().get("/projects/").content.decode()
    embed = Client().get(f"/projects/embed/{event.slug}/").content.decode()
    assert marker in embed
    assert "<a " not in embed
