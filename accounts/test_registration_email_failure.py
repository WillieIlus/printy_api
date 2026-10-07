import logging

from allauth.account.models import EmailAddress
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User

UNREACHABLE_SMTP_BACKEND = "accounts.test_registration_email_failure.UnreachableSMTPBackend"


class UnreachableSMTPBackend(BaseEmailBackend):
    """Reproduces the production droplet being unable to reach smtp.gmail.com:587."""

    def send_messages(self, email_messages):
        raise TimeoutError("timed out")


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND=UNREACHABLE_SMTP_BACKEND,
    FRONTEND_URL="https://printy.ke",
)
class RegistrationSurvivesActivationEmailFailureTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.email = "email-outage@test.com"
        self.payload = {
            "email": self.email,
            "password": "Pass12345",
            "name": "Email Outage",
            "role": "client",
        }

    def test_registration_returns_201_and_keeps_the_account(self):
        with self.assertLogs("api.auth", level=logging.ERROR) as logs:
            response = self.client.post("/api/auth/register/", self.payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["email"], self.email)
        self.assertTrue(body["verification_required"])
        self.assertIn("registration_activation_email_failed", logs.output[0])

        user = User.objects.get(email=self.email)
        email_address = EmailAddress.objects.get(user=user, email=self.email)
        self.assertTrue(email_address.primary)
        self.assertFalse(email_address.verified)

    def test_retrying_the_same_email_is_a_validation_error_not_a_500(self):
        self.client.post("/api/auth/register/", self.payload, format="json")

        response = self.client.post("/api/auth/register/", self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["field_errors"])

    def test_resend_confirmation_reports_failure_instead_of_raising(self):
        self.client.post("/api/auth/register/", self.payload, format="json")

        with self.assertLogs("api.auth", level=logging.ERROR):
            response = self.client.post(
                "/api/auth/email/resend/", {"email": self.email}, format="json"
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["sent"])

    def test_activation_email_is_still_sent_when_the_backend_works(self):
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            response = self.client.post("/api/auth/register/", self.payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("printy.ke/auth/confirm-email?key=", mail.outbox[0].body)
