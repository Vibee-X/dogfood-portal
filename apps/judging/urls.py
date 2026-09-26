from django.urls import path
from . import views

app_name = "judging"

urlpatterns = [
    path("judge/scores", views.judge_scores, name="judge_scores"),
    path("export.csv", views.csv_export, name="csv_export"),
]
