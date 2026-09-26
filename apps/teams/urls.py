from django.urls import path
from . import views

app_name = "teams"

urlpatterns = [
    path("event/<slug:event_slug>/create/", views.team_create, name="team_create"),
    path("<int:pk>/", views.team_detail, name="team_detail"),
    path("<int:pk>/invite/", views.generate_invite, name="generate_invite"),
    path("<int:pk>/leave/", views.leave_team, name="leave_team"),
    path("join/<str:code>/", views.join_team, name="join_team"),
]
