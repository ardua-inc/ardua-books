"""
Tests for the authentication surface exposed by mounting the full allauth
URLconf.

These guard configuration rather than application logic. Every failure mode
covered here is one that fails *silently* -- a wrong or unrecognised setting
name leaves the app running happily with the insecure default -- so the
assertions are behavioural (what does the endpoint actually do) rather than
assertions about settings values.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()

SIGNUP_URL = "/accounts/signup/"


def test_signup_url_is_mounted():
    """
    Guards the rest of this module.

    If allauth's URLconf stops being mounted at /accounts/, the signup tests
    below would pass trivially against a 404 and stop proving anything. The
    original review of this endpoint checked /signup/ -- which 404s because
    allauth lives under /accounts/ -- and wrongly concluded signup was closed.
    """
    assert reverse("account_signup") == SIGNUP_URL


@pytest.mark.django_db
def test_signup_page_does_not_offer_a_registration_form(client):
    """The signup view must not render a usable registration form."""
    response = client.get(SIGNUP_URL)

    assert response.status_code == 200
    body = response.content.decode("utf-8", "ignore")
    assert 'type="password"' not in body, (
        "The signup page is rendering a password field, so self-service "
        "registration is open."
    )


@pytest.mark.django_db
def test_signup_post_does_not_create_a_user(client):
    """
    Posting registration data must not create an account.

    Asserting on the POST as well as the GET matters: hiding the form without
    closing the view would still leave the endpoint exploitable directly.

    The payload is built from the signup form's own field list rather than
    hardcoded. An earlier version of this test omitted `username` and so was
    rejected with "this field is required" -- it passed even with signup wide
    open, proving nothing. Deriving the fields keeps the test honest if the
    allauth upgrade changes them.
    """
    from allauth.account.forms import SignupForm

    password = "a-sufficiently-long-passphrase"
    payload = {
        "email": "intruder@example.com",
        "username": "intruder",
        "password1": password,
        "password2": password,
    }
    missing = set(SignupForm().fields) - set(payload)
    assert not missing, f"Signup form gained fields this test does not fill: {missing}"

    before = User.objects.count()
    client.post(SIGNUP_URL, payload)

    assert User.objects.count() == before
    assert not User.objects.filter(email__iexact="intruder@example.com").exists()


@pytest.mark.django_db
def test_authenticated_users_are_redirected_away_from_signup(client):
    """
    Documents why signup must only ever be assessed anonymously.

    allauth's SignupView inherits RedirectAuthenticatedUserMixin, whose
    dispatch() redirects logged-in users to LOGIN_REDIRECT_URL *before* the
    closed-signup check runs. So a logged-in browser gets 302 -> / whether
    registration is open or closed, which twice led to the wrong conclusion
    that the endpoint was inactive.

    This test asserts the redirect exists so the trap stays visible; the tests
    above deliberately use the anonymous client, which is the only view that
    reflects what an outsider can reach.
    """
    user = User.objects.create_user(
        username="staffer",
        email="staffer@example.com",
        password="a-sufficiently-long-passphrase",
    )
    client.force_login(user)

    response = client.get(SIGNUP_URL)

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_account_adapter_closes_signup():
    """The adapter is the mechanism; assert it directly as well."""
    from allauth.account.adapter import get_adapter

    assert get_adapter().is_open_for_signup(None) is False


def test_login_is_by_email_on_the_installed_allauth():
    """
    Login must be email-based on whichever allauth version is installed.

    settings.py sets both ACCOUNT_LOGIN_METHODS (allauth >= 64.1) and
    ACCOUNT_AUTHENTICATION_METHOD (earlier). Reading the resolved app_settings
    value proves the one this version actually honours took effect, instead of
    silently falling back to allauth's "username" default.
    """
    from allauth.account import app_settings

    if hasattr(app_settings, "LOGIN_METHODS"):
        assert app_settings.LOGIN_METHODS == frozenset({"email"})
    else:
        assert app_settings.AUTHENTICATION_METHOD == "email"
