from allauth.account.models import EmailAddress
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://printy.ke",
)
class LoginEmailCaseRegressionTestCase(TestCase):
    """Bug 2: email case must never decide whether a valid account can log in."""

    def setUp(self):
        self.client = APIClient()

    def _register(self, email):
        return self.client.post(
            "/api/auth/register/",
            {"email": email, "password": "Pass12345", "name": "Case Test", "role": "client"},
            format="json",
        )

    def _mark_verified(self, email):
        EmailAddress.objects.filter(email__iexact=email).update(verified=True)

    def test_register_mixed_case_then_lowercase_login_succeeds(self):
        registered = self._register("John.Doe@Gmail.com")
        self.assertEqual(registered.status_code, 201)
        self._mark_verified("John.Doe@Gmail.com")

        login = self.client.post(
            "/api/auth/login/",
            {"email": "john.doe@gmail.com", "password": "Pass12345"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.json())
        self.assertIn("access", login.json())

    def test_register_lowercase_then_mixed_case_login_succeeds(self):
        self._register("jane@example.com")
        self._mark_verified("jane@example.com")

        login = self.client.post(
            "/api/auth/login/",
            {"email": "Jane@Example.com", "password": "Pass12345"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.json())

    def test_exact_original_case_login_succeeds(self):
        self._register("Sam@example.com")
        self._mark_verified("Sam@example.com")

        login = self.client.post(
            "/api/auth/login/",
            {"email": "Sam@example.com", "password": "Pass12345"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.json())

    def test_registration_stores_lowercase_email(self):
        self._register("Mixed.Case@Example.COM")

        user = User.objects.get(email__iexact="mixed.case@example.com")
        self.assertEqual(user.email, "mixed.case@example.com")
        self.assertTrue(
            EmailAddress.objects.filter(user=user, email="mixed.case@example.com").exists()
        )

    def test_duplicate_email_registration_is_case_insensitive(self):
        first = self._register("Dup@Example.com")
        self.assertEqual(first.status_code, 201)

        second = self._register("dup@example.com")
        self.assertEqual(second.status_code, 400, second.json())

    def test_legacy_mixed_case_row_still_logs_in(self):
        """A row written before the fix keeps its case; iexact fallback must reach it."""

        user = User.objects.create_user(
            email="Legacy.Mixed@Example.com",
            password="Pass12345",
            name="Legacy",
            role=User.Role.CLIENT,
        )
        user.email = "Legacy.Mixed@Example.com"
        user.save(update_fields=["email"])
        EmailAddress.objects.create(
            user=user,
            email="Legacy.Mixed@Example.com",
            primary=True,
            verified=True,
        )

        login = self.client.post(
            "/api/auth/login/",
            {"email": "legacy.mixed@example.com", "password": "Pass12345"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.json())
