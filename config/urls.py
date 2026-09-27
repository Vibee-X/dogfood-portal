"""URL configuration for Dogfood Portal."""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("events/", include("apps.events.urls")),
    path("events/", include("apps.judging.web_urls")),
    path("teams/", include("apps.teams.urls")),
    path("projects/", include("apps.submissions.urls")),
    path("api/", include("apps.judging.urls")),
    path("api/", include("apps.voting.urls")),
    path("", include("apps.core.urls")),
]
