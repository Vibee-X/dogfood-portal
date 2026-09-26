from django.urls import path
from . import views

app_name = "judging"

urlpatterns = [
    path("judge/scores", views.judge_scores, name="judge_scores"),
    path("export.csv", views.csv_export, name="csv_export"),
    path("judging/rubrics", views.rubrics, name="rubrics"),
    path("judging/rubrics/<int:rubric_id>/criteria", views.rubric_criteria, name="rubric_criteria"),
    path("judging/judges/invite", views.invite_judge, name="invite_judge"),
    path("judging/assignments/generate", views.generate_judge_assignments, name="generate_assignments"),
    path("judging/progress", views.judge_progress_api, name="judge_progress"),
    path("judging/normalization/run", views.normalize_scores, name="normalize_scores"),
]
