# billing/context_processors.py

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

