from django.urls import path

from . import views

app_name = "voting"

urlpatterns = [
    path("voting/ballot", views.ballot, name="ballot"),
    path("voting/votes", views.cast_vote, name="cast_vote"),
    path("voting/results", views.voting_results, name="voting_results"),
    path("projects/<int:submission_id>/comments", views.comments, name="comments"),
    path("comments/<int:comment_id>", views.edit_comment, name="edit_comment"),
    path("comments/<int:comment_id>/moderation", views.moderate_comment, name="moderate_comment"),
]
