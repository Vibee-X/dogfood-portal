from django.urls import path

from . import views


app_name = "judging_web"

urlpatterns = [
    path("<slug:slug>/judging/progress/", views.progress_dashboard, name="progress_dashboard"),
    path("<slug:slug>/judging/pairwise/", views.pairwise_dashboard, name="pairwise_dashboard"),
]
