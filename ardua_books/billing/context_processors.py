# billing/context_processors.py


def is_viewer(request):
    """
    Expose 'is_viewer' flag in templates to check if user has read-only access.
    Users in the 'Viewer' group are read-only and should not see mutation buttons.
    """
    if not request.user.is_authenticated:
        return {"is_viewer": False}
    return {"is_viewer": request.user.groups.filter(name="Viewer").exists()}


def mobile_flag(request):
    """
    Expose 'mobile' flag in templates if the request path is under /m/.
    This lets base.html render a minimal header/nav for the PWA shell.
    """
    path = request.path or ""
    if path.startswith("/m/"):
        return {"mobile": True}

    # If we're on the login page but will return to /m/,
    # also show the mobile header.
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    if path.startswith("/accounts/login") and next_url.startswith("/m/"):
        return {"mobile": True}

    return {"mobile": False}

