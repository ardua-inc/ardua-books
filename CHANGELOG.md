# Changelog

All notable changes to Ardua Books are recorded here. The canonical version
number lives in `ardua_books/ardua_books/settings.py` (`VERSION`) and is
surfaced by the `/about/` page.

## [3.3.1] - 2026-08-11

- Consolidated dependencies into a single `ardua_books/requirements.txt`; removed the drifted root-level copy that pinned a different Django.
- Corrected the Django pin from `==5.0,<6.0` (an exact pin to end-of-life 5.0) to `>=5.2,<5.3`.
- Pinned `django-allauth[socialaccount]` so the SSO dependencies survive the pending upgrade to allauth 65.x.
- `DJANGO_DEBUG` now defaults to False instead of True, so a deployment with no value set fails closed.
- Closed self-service registration at `/accounts/signup/` with `NoSignupAccountAdapter`; removed the no-op `ACCOUNT_ALLOW_REGISTRATION` setting that was believed to be doing this.
- Set `ACCOUNT_AUTHENTICATION_METHOD` alongside `ACCOUNT_LOGIN_METHODS` so email login works on both the pinned and the upcoming allauth.
- Added `tests/test_auth_access.py` covering the authentication surface.
- Fixed two long-standing invoice test failures: they asserted on `Invoice.sequence`, which stopped being assigned when invoice numbering was reworked. Rewritten to assert on `invoice_number`; the numbering logic itself is unchanged.

## [3.3.0] - baseline

- Baseline entry. Changes prior to 3.3.1 were not tracked in this file; see
  the git history for detail.
