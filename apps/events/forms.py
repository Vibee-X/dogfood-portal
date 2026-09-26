from django import forms
from .models import Event, Track, Prize


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "name", "slug", "description", "start_date", "end_date",
            "submission_deadline", "judging_start", "judging_end",
            "voting_start", "voting_end", "is_published", "reviews_per_submission",
        ]
        widgets = {
            "start_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "submission_deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "judging_start": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "judging_end": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "voting_start": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "voting_end": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class TrackForm(forms.ModelForm):
    class Meta:
        model = Track
        fields = ["name", "description"]


class PrizeForm(forms.ModelForm):
    class Meta:
        model = Prize
        fields = ["name", "description", "track", "rank"]
