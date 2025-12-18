from django.shortcuts import render
from django.conf import settings


def home(request):
    """Application home page with navigation to both modules."""
    return render(request, "home.html")


def about(request):
    """About page showing version information."""
    # Combine semantic version with git SHA
    git_sha = settings.APP_VERSION
    git_sha_short = git_sha[:7] if len(git_sha) > 7 else git_sha

    # Full version: "1.0.0-abc1234" or "1.0.0-dev"
    full_version = f"{settings.VERSION}-{git_sha_short}"

    return render(request, "about.html", {
        "version": settings.VERSION,
        "git_sha": git_sha,
        "git_sha_short": git_sha_short,
        "full_version": full_version,
    })
