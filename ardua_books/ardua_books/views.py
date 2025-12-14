from django.shortcuts import render


def home(request):
    """Application home page with navigation to both modules."""
    return render(request, "home.html")
