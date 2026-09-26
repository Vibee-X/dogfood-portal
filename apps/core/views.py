from django.shortcuts import render, redirect


def home(request):
    """Home page — redirects to the project gallery."""
    return redirect("submissions:gallery")
