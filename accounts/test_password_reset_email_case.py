"""Phase 3A Task D: password reset and other email-keyed auth paths must work
for legacy mixed-case accounts when the request uses a different casing.

D1: tests only. D2: fix only what fails, minimally, on the auth path.
"""

from urllib.parse import parse_qs, urlparse

from allauth.account.models import EmailAddress
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://printy.test",
)
class PasswordResetEmailCaseTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _legacy_user(self, email):
        """Create a user the way a pre-fix row could exist: mixed-case on disk."""
        user = User.objects.create_user(
            email=email,
            password="Pass12345",
            name="Legacy",
            role=User.Role.CLIENT,
        )
        # Force the legacy casing back onto the row (create_user lowercases now).
        user.email = email
        user.save(update_fields=["email"])
        EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
        return user

    def _reset_key_from_last_email(self):
        self.assertGreaterEqual(len(mail.outbox), 1)
        body = mail.outbox[-1].body
        link = next(
            part
            for part in body.split()
            if part.startswith("https://printy.test/auth/reset-password")
        )
        return parse_qs(urlparse(link).query)["key"][0]

    def test_lowercase_request_resets_legacy_mixed_case_account(self):
        self._legacy_user("Legacy.User@Example.com")

        request = self.client.post(
            "/api/auth/password-reset/",
            {"email": "legacy.user@example.com"},
            format="json",
        )
        self.assertEqual(request.status_code, 200, request.json())

        key = self._reset_key_from_last_email()
        confirm = self.client.post(
            "/api/auth/password-reset/confirm/",
            {"key": key, "password": "NewPass12345"},
            format="json",
        )
        self.assertEqual(confirm.status_code, 200, confirm.json())

        login = self.client.post(
            "/api/auth/login/",
            {"email": "legacy.user@example.com", "password": "NewPass12345"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.json())

    def test_mixed_case_request_resets_lowercase_account(self):
        user = User.objects.create_user(
            email="lower.acct@example.com",
            password="Pass12345",
            name="Lower",
            role=User.Role.CLIENT,
        )
        EmailAddress.objects.create(
            user=user, email="lower.acct@example.com", primary=True, verified=True
        )

        request = self.client.post(
            "/api/auth/password-reset/",
            {"email": "Lower.Acct@Example.com"},
            format="json",
        )
        self.assertEqual(request.status_code, 200, request.json())

        key = self._reset_key_from_last_email()
        confirm = self.client.post(
            "/api/auth/password-reset/confirm/",
            {"key": key, "password": "NewPass12345"},
            format="json",
        )
        self.assertEqual(confirm.status_code, 200, confirm.json())

    def test_unknown_email_still_returns_generic_success(self):
        response = self.client.post(
            "/api/auth/password-reset/",
            {"email": "nobody@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://printy.test",
)
class ResendConfirmationEmailCaseTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _legacy_unverified_user(self, email):
        user = User.objects.create_user(
            email=email,
            password="Pass12345",
            name="Legacy",
            role=User.Role.CLIENT,
        )
        user.email = email
        user.save(update_fields=["email"])
        EmailAddress.objects.create(user=user, email=email, primary=True, verified=False)
        return user

    def test_resend_with_lowercase_request_reaches_legacy_mixed_case_account(self):
        self._legacy_unverified_user("Resend.Legacy@Example.com")

        response = self.client.post(
            "/api/auth/resend-confirmation/",
            {"email": "resend.legacy@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.assertGreaterEqual(len(mail.outbox), 1)
