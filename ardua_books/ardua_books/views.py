from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_safe


def home(request):
    """Application home page with navigation to both modules."""
    return render(request, "home.html")


def about(request):
    """About page showing version information."""
    from datetime import datetime

    # Combine semantic version with git SHA
    git_sha = settings.APP_VERSION
    git_sha_short = git_sha[:7] if len(git_sha) > 7 else git_sha

    # Full version: "1.0.0-abc1234" or "1.0.0-dev"
    full_version = f"{settings.VERSION}-{git_sha_short}"

    # Parse commit date if available (ISO 8601 format from git log -1 --format=%cI)
    commit_date = None
    if settings.APP_COMMIT_DATE:
        try:
            commit_date = datetime.fromisoformat(settings.APP_COMMIT_DATE)
        except ValueError:
            pass

    return render(request, "about.html", {
        "version": settings.VERSION,
        "git_sha": git_sha,
        "git_sha_short": git_sha_short,
        "full_version": full_version,
        "commit_date": commit_date,
    })


@require_safe
@cache_control(public=True, max_age=86400)
def favicon(request):
    """
    Serve the favicon at the site root.

    Safari requests /favicon.ico directly, ignoring <link rel="icon"> tags, so
    this must answer 200 here rather than only under /static/. The file is
    located with the staticfiles finders (the source tree's static/ dir), so it
    works the same with or without collectstatic having run.
    """
    path = finders.find("favicon/favicon.ico")
    if not path:
        raise Http404("favicon not found")
    return FileResponse(open(path, "rb"), content_type="image/x-icon")
