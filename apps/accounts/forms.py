from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)
    display_name = forms.CharField(max_length=255, required=False)

    class Meta:
        model = User
        fields = ("username", "email", "display_name", "password1", "password2")
