from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("certificates/event/<slug:event_slug>/issue/", views.issue_participation_certificate, name="certificate_issue"),
    path("certificates/<str:verification_code>/", views.certificate_verify, name="certificate_verify"),
]
