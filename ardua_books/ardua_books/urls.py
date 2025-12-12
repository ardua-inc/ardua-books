from django.contrib import admin
from django.urls import path, include, re_path
from django.shortcuts import redirect
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve


def root_redirect(request):
    return redirect("billing:client_list")


urlpatterns = [
    path("", root_redirect, name="home"),
    path("admin/", admin.site.urls),
    path("", include("billing.urls", namespace="billing")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounting/", include("accounting.urls")),

]

# Serve uploaded media files from MEDIA_ROOT at MEDIA_URL, even when DEBUG=False.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]

