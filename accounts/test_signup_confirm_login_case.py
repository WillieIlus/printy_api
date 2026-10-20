from urllib.parse import parse_qs, urlparse

from allauth.account.models import EmailAddress
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://printy.test",
)
class SignupConfirmLoginCaseEndToEndTests(TestCase):
    """Bug 1 + Bug 2 together: a mixed-case signup must be able to confirm its
    email via the emailed link and then log in with any casing of its email."""

    def setUp(self):
        self.client = APIClient()

    def _register(self, email):
        return self.client.post(
            "/api/auth/register/",
            {
                "email": email,
                "password": "Pass12345",
                "name": "Round Trip",
                "role": "client",
            },
            format="json",
        )

    def _emailed_key(self):
        self.assertGreaterEqual(len(mail.outbox), 1)
        body = mail.outbox[-1].body
        link = next(
            part
            for part in body.split()
            if part.startswith("https://printy.test/auth/confirm-email")
        )
        return parse_qs(urlparse(link).query)["key"][0]

    def test_mixed_case_signup_confirms_then_logs_in_any_case(self):
        registered = self._register("Case.RoundTrip@Example.com")
        self.assertEqual(registered.status_code, 201)

        unverified = self.client.post(
            "/api/auth/login/",
            {"email": "case.roundtrip@example.com", "password": "Pass12345"},
            format="json",
        )
        self.assertEqual(unverified.status_code, 400)
        self.assertEqual(unverified.json()["code"], "EMAIL_UNVERIFIED")

        key = self._emailed_key()
        confirmed = self.client.post("/api/auth/confirm-email/", {"key": key}, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.json())
        self.assertTrue(confirmed.json()["verified"])
        self.assertTrue(
            EmailAddress.objects.get(email__iexact="case.roundtrip@example.com").verified
        )

        for typed in (
            "case.roundtrip@example.com",
            "Case.RoundTrip@Example.com",
            "CASE.ROUNDTRIP@EXAMPLE.COM",
        ):
            with self.subTest(typed=typed):
                login = self.client.post(
                    "/api/auth/login/",
                    {"email": typed, "password": "Pass12345"},
                    format="json",
                )
                self.assertEqual(login.status_code, 200, login.json())
                self.assertIn("access", login.json())

    def test_confirmation_link_key_round_trips_through_email(self):
        self._register("link.roundtrip@example.com")
        key = self._emailed_key()

        response = self.client.post("/api/auth/confirm-email/", {"key": key}, format="json")

        self.assertEqual(response.status_code, 200, response.json())
        self.assertFalse(response.json()["already_verified"])

    def test_invalid_confirmation_key_returns_token_invalid(self):
        response = self.client.post(
            "/api/auth/confirm-email/",
            {"key": "not-a-valid-signature"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "TOKEN_INVALID")
