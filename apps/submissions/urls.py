from django.urls import path
from . import views

app_name = "submissions"

urlpatterns = [
    path("", views.gallery, name="gallery"),
    path("embed/<slug:slug>/", views.event_embed_gallery, name="event_embed_gallery"),
    path("new", views.api_submit_project, name="api_submit"),
    path("<int:pk>/", views.project_detail, name="project_detail"),
    path("event/<slug:event_slug>/submit/", views.submission_create, name="submission_create"),
    path("<int:pk>/edit/", views.submission_edit, name="submission_edit"),
]
