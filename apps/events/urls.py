from django.urls import path
from . import views

app_name = "events"

urlpatterns = [
    path("", views.event_list, name="event_list"),
    path("create/", views.event_create, name="event_create"),
    path("<slug:slug>/", views.event_detail, name="event_detail"),
    path("<slug:slug>/edit/", views.event_edit, name="event_edit"),
    path("<slug:slug>/audit/", views.event_audit, name="event_audit"),
    path("<slug:event_slug>/tracks/add/", views.track_create, name="track_create"),
    path("<slug:event_slug>/prizes/add/", views.prize_create, name="prize_create"),
]
