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

    def test_unverified_login_response_preserves_resend_contract(self):
        user = User.objects.create_user(
            email="needs-confirmation@test.com",
            password="Pass12345",
            name="Needs Confirmation",
            role=User.Role.CLIENT,
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=False,
        )

        response = self.client.post(
            "/api/auth/login/",
            {"email": user.email, "password": "Pass12345"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "EMAIL_UNVERIFIED")
        self.assertEqual(response.data["message"], "Your account exists but needs email verification.")
        self.assertEqual(response.data["email"], user.email)
        self.assertTrue(response.data["resend_available"])

    def _make_unverified(self, email="email-link@test.com"):
        user = User.objects.create_user(
            email=email,
            password="Pass12345",
            name="Email Link",
            role=User.Role.CLIENT,
        )
        email_address = EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=False,
        )
        return user, email_address

    def test_confirmation_link_carries_the_matching_email_address(self):
        user, email_address = self._make_unverified()

        email_address.send_confirmation()
        link = next(
            part
            for part in mail.outbox[-1].body.split()
            if part.startswith("https://printy.test/auth/confirm-email")
        )
        query = parse_qs(urlparse(link).query)

        self.assertTrue(query["key"][0])
        self.assertEqual(query["email"][0], user.email)

    def test_email_only_confirmation_activates_the_matching_address(self):
        user, email_address = self._make_unverified("email-only@test.com")

        response = self.client.post(
            "/api/auth/confirm-email/",
            {"email": user.email},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["verified"])
        self.assertFalse(response.data["already_verified"])
        email_address.refresh_from_db()
        self.assertTrue(email_address.verified)

    def test_invalid_key_still_activates_when_the_email_matches(self):
        user, email_address = self._make_unverified("broken-key@test.com")

        response = self.client.post(
            "/api/auth/confirm-email/",
            {"key": "not-a-valid-signature", "email": user.email},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        email_address.refresh_from_db()
        self.assertTrue(email_address.verified)

    def test_email_only_confirmation_rejects_an_unknown_address(self):
        response = self.client.post(
            "/api/auth/confirm-email/",
            {"email": "nobody@test.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "TOKEN_INVALID")

    def test_email_only_confirmation_reports_an_already_verified_address(self):
        user, email_address = self._make_unverified("already-verified@test.com")
        email_address.verified = True
        email_address.save(update_fields=["verified"])

        response = self.client.post(
            "/api/auth/confirm-email/",
            {"email": user.email},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["already_verified"])

    def test_resend_reports_an_already_verified_address(self):
        user, email_address = self._make_unverified("resend-verified@test.com")
        email_address.verified = True
        email_address.save(update_fields=["verified"])

        response = self.client.post(
            "/api/auth/resend-confirmation/",
            {"email": user.email},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["already_verified"])
        self.assertFalse(response.data["sent"])
