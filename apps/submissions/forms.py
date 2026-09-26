from django import forms
from .models import Submission


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = [
            "title", "tagline", "summary", "description",
            "repo_url", "live_url", "demo_video_url", "track",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 6}),
            "summary": forms.Textarea(attrs={"rows": 3}),
        }
