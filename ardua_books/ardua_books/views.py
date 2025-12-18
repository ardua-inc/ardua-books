from django.shortcuts import render
from django.conf import settings


def home(request):
    """Application home page with navigation to both modules."""
    return render(request, "home.html")


def about(request):
    """About page showing version information."""
    return render(request, "about.html", {
        "version": settings.APP_VERSION,
        "version_short": settings.APP_VERSION[:7] if len(settings.APP_VERSION) > 7 else settings.APP_VERSION,
    })
