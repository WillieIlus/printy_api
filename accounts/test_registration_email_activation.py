from urllib.parse import parse_qs, urlparse

from allauth.account.models import EmailAddress
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://printy.ke",
)
class RegistrationEmailActivationFlowTestCase(TestCase):
    """End-to-end contract: register → activation email sent → login blocked → confirm → login succeeds.

    This is the live deployment-readiness contract for the email activation path.
    """

    def setUp(self):
        self.client = APIClient()
        self.email = "activation-flow@test.com"
        self.password = "Pass12345"

    def _register(self, email=None):
        return self.client.post(
            "/api/auth/register/",
            {
                "email": email or self.email,
                "password": self.password,
                "name": "Activation Test",
                "role": "client",
            },
            format="json",
        )

    def _extract_confirmation_key(self, body: str) -> str:
        for token in body.split():
            if "key=" not in token:
                continue
            key = parse_qs(urlparse(token.strip()).query).get("key", [None])[0]
            if key:
                return key
        self.fail("Could not extract confirmation key from email body.")

    def test_register_sends_verification_email_with_production_link(self):
        response = self._register()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["verification_required"], True)
        self.assertEqual(response.json()["resend_available"], True)
        self.assertEqual(len(mail.outbox), 1)

        msg = mail.outbox[0]
        self.assertIn("verify", msg.subject.lower())
        self.assertIn("printy.ke/auth/confirm-email?key=", msg.body)
        self.assertNotIn("example.com", msg.body)
        self.assertNotIn("localhost:3000", msg.body)
        self.assertNotIn("localhost:8000", msg.body)

        link = next(token for token in msg.body.split() if "key=" in token)
        parsed = urlparse(link.strip())
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "printy.ke")
        self.assertEqual(parsed.path, "/auth/confirm-email")

    def test_login_blocked_until_email_confirmed(self):
        self._register()

        login_response = self.client.post(
            "/api/auth/token/",
            {"email": self.email, "password": self.password},
            format="json",
        )

        self.assertEqual(login_response.status_code, 400)
        body = login_response.json()
        self.assertEqual(body["code"], "EMAIL_UNVERIFIED")
        self.assertEqual(body["email"], self.email)
        self.assertTrue(body["resend_available"])

        email_address = EmailAddress.objects.get(email=self.email)
        self.assertFalse(email_address.verified)

    def test_full_resend_confirm_login_flow(self):
        self._register()

        resend = self.client.post(
            "/api/auth/email/resend/",
            {"email": self.email},
            format="json",
        )
        self.assertEqual(resend.status_code, 200)
        self.assertTrue(resend.json()["sent"])
        self.assertEqual(len(mail.outbox), 2)

        key = self._extract_confirmation_key(mail.outbox[-1].body)
        confirm = self.client.post(
            "/api/auth/email/verify/",
            {"key": key},
            format="json",
        )
        self.assertEqual(confirm.status_code, 200)
        self.assertTrue(confirm.json()["verified"])

        email_address = EmailAddress.objects.get(email=self.email)
        self.assertTrue(email_address.verified)

        login = self.client.post(
            "/api/auth/token/",
            {"email": self.email, "password": self.password},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn("access", login.json())
