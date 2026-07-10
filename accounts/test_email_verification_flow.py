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
class EmailVerificationFlowTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_confirmation_endpoint_accepts_token_alias_from_frontend(self):
        user = User.objects.create_user(
            email="verify-token@test.com",
            password="Pass12345",
            name="Verify Token",
            role=User.Role.CLIENT,
        )
        email_address = EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=False,
        )

        email_address.send_confirmation()
        self.assertEqual(len(mail.outbox), 1)

        body = mail.outbox[0].body
        link = next(part for part in body.split() if part.startswith("https://printy.test/auth/confirm-email"))
        key = parse_qs(urlparse(link).query)["key"][0]

        response = self.client.post("/api/auth/confirm-email/", {"token": key}, format="json")

        self.assertEqual(response.status_code, 200)
        email_address.refresh_from_db()
        self.assertTrue(email_address.verified)
        self.assertEqual(response.data["email"], user.email)
        self.assertTrue(response.data["verified"])
