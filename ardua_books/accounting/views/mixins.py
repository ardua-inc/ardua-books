"""
Reusable view mixins for the accounting app.
"""

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied


class ReadOnlyUserMixin(AccessMixin):
    """
    Mixin that restricts users in the 'Viewer' group to read-only access.

    Users in the Viewer group can access GET requests but receive a
    PermissionDenied error on any mutating request (POST, PUT, DELETE, etc.).

    Usage:
        class MyCreateView(ReadOnlyUserMixin, LoginRequiredMixin, CreateView):
            ...

    Note: Place this mixin BEFORE LoginRequiredMixin in the inheritance chain.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if request.user.groups.filter(name="Viewer").exists():
                # Allow GET and HEAD requests (read-only)
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    raise PermissionDenied(
                        "You have read-only access and cannot make changes."
                    )
        return super().dispatch(request, *args, **kwargs)


def is_viewer(user):
    """
    Template helper to check if user is a read-only viewer.
    Returns True if user is in the Viewer group.
    """
    if not user.is_authenticated:
        return False
    return user.groups.filter(name="Viewer").exists()


def readonly_user_check(view_func):
    """
    Decorator for function-based views that restricts Viewer group to read-only.

    Usage:
        @login_required
        @readonly_user_check
        def my_view(request, pk):
            ...
    """
    from functools import wraps

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            if request.user.groups.filter(name="Viewer").exists():
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    raise PermissionDenied(
                        "You have read-only access and cannot make changes."
                    )
        return view_func(request, *args, **kwargs)

    return wrapper


class FilterPersistenceMixin:
    """
    Mixin that persists filter parameters in the session.

    When a user applies filters, they're saved to the session.
    When returning to the page without explicit filters, the saved filters are restored.

    Usage:
        class MyListView(FilterPersistenceMixin, ListView):
            filter_persistence_key = "my_list_filters"
            filter_params = ["date_preset", "date_from", "date_to", "status"]

    The mixin will:
    - Save filter params to session when they're in the URL
    - Redirect to restore filters when returning without params (unless ?clear=1)
    """

    filter_persistence_key = None  # Override in subclass, e.g., "bank_register_filters"
    filter_params = []  # List of GET param names to persist

    def get_filter_persistence_key(self):
        """Get the session key for storing filters. Can include dynamic parts."""
        return self.filter_persistence_key

    def dispatch(self, request, *args, **kwargs):
        # Skip filter logic for non-GET requests
        if request.method != "GET":
            return super().dispatch(request, *args, **kwargs)

        key = self.get_filter_persistence_key()
        if not key or not self.filter_params:
            return super().dispatch(request, *args, **kwargs)

        # Check if user wants to clear filters
        if request.GET.get("clear") == "1":
            request.session.pop(key, None)
            # Redirect to clean URL without ?clear=1
            from django.shortcuts import redirect
            return redirect(request.path)

        # Check if any filter params are in the URL
        has_filters = any(request.GET.get(p) for p in self.filter_params)

        if has_filters:
            # Save current filters to session
            filters = {p: request.GET.get(p) for p in self.filter_params if request.GET.get(p)}
            request.session[key] = filters
        else:
            # No filters in URL - check if we have saved filters to restore
            saved_filters = request.session.get(key)
            if saved_filters:
                # Redirect to URL with saved filters
                from django.http import QueryDict
                from django.shortcuts import redirect

                qd = QueryDict(mutable=True)
                for param, value in saved_filters.items():
                    qd[param] = value

                return redirect(f"{request.path}?{qd.urlencode()}")

        return super().dispatch(request, *args, **kwargs)
