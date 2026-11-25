"""
Tests for the favicon: the root /favicon.ico route and the <link> tags.

Safari requests /favicon.ico at the site root regardless of <link> tags, so
the route must answer 200 with the real icon (not the 0-byte placeholder this
replaced). Every other browser follows the <link> tags, so each href they
point at must resolve too.
"""

import re

import pytest
from django.contrib.staticfiles import finders

ICON_LINK_RE = re.compile(
    r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"'
)

EXPECTED_STATIC_FILES = [
    "favicon/favicon.ico",
    "favicon/favicon.svg",
    "favicon/favicon-32.png",
    "favicon/apple-touch-icon.png",
]


@pytest.mark.parametrize("name", EXPECTED_STATIC_FILES)
def test_generated_favicon_files_exist_and_are_not_empty(name):
    path = finders.find(name)
    assert path, f"{name} is not discoverable by the staticfiles finders"
    with open(path, "rb") as fh:
        assert fh.read(1), f"{name} is empty"


def test_root_favicon_ico_is_served_without_login(client):
    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response["Content-Type"] == "image/x-icon"
    body = b"".join(response.streaming_content)
    # ICO header: reserved=0, type=1 (icon)
    assert body[:4] == b"\x00\x00\x01\x00"
    with open(finders.find("favicon/favicon.ico"), "rb") as fh:
        assert body == fh.read()


def test_root_favicon_ico_rejects_post(client):
    assert client.post("/favicon.ico").status_code == 405


@pytest.mark.django_db
def test_base_page_has_all_four_icon_links(client):
    # The login page extends base.html and needs no authentication.
    response = client.get("/accounts/login/")
    assert response.status_code == 200
    body = response.content.decode()

    assert 'rel="icon"' in body
    assert 'rel="apple-touch-icon"' in body
    hrefs = ICON_LINK_RE.findall(body)
    assert "/favicon.ico" in hrefs
    assert len(hrefs) == 4, hrefs


@pytest.mark.django_db
def test_every_icon_link_href_resolves(client):
    body = client.get("/accounts/login/").content.decode()
    hrefs = ICON_LINK_RE.findall(body)
    assert hrefs

    for href in hrefs:
        if href == "/favicon.ico":
            assert client.get(href).status_code == 200
            continue
        assert href.startswith("/static/"), href
        name = href.removeprefix("/static/")
        assert finders.find(name), f"{href} does not resolve to a static file"


@pytest.mark.django_db
def test_mobile_page_gets_favicon(client, user):
    # mobile_home.html extends base.html, so it inherits the tags. (Its
    # {% block extra_head %} PWA tags do not render: base.html defines no
    # such block. That is pre-existing and deliberately not changed here.)
    client.force_login(user)
    response = client.get("/m/")
    assert response.status_code == 200
    body = response.content.decode()

    assert 'href="/favicon.ico"' in body
    assert len(ICON_LINK_RE.findall(body)) == 4
