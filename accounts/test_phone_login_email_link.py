"""Phone sign-in resolves to the account's email identity when it can.

A phone captured during self-signup is never verified on its own. It may
still identify an account whose email address is already trusted, so the
same person can sign in with either identifier.
"""
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.services.phone import set_profile_phone

User = get_user_model()

PASSWORD = "phone-login-pass-1"


def _make_user(email: str, phone: str, *, email_verified: bool | None = None):
    user = User.objects.create_user(
        username=email,
        email=email,
        password=PASSWORD,
        role=User.Role.CLIENT,
    )
    set_profile_phone(user, phone, verified=False)
    if email_verified is not None:
        EmailAddress.objects.create(
            user=user,
            email=email,
            verified=email_verified,
            primary=True,
        )
    return user


@override_settings(ACCOUNT_EMAIL_VERIFICATION="mandatory")
class PhoneLoginEmailLinkTests(TestCase):
    def setUp(self):
        self.api = APIClient()

    def _login(self, phone: str, password: str = PASSWORD):
        return self.api.post(
            "/api/auth/login/",
            {"phone": phone, "password": password},
            format="json",
        )

    def test_unverified_phone_with_verified_email_can_log_in(self):
        user = _make_user(
            "linked-phone@printy.test", "0700100001", email_verified=True
        )

        response = self._login("0700100001")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["access"])
        self.assertEqual(response.data["user"]["id"], user.id)
        self.assertEqual(response.data["user"]["email"], user.email)

    def test_verified_phone_can_log_in_without_an_email_address(self):
        user = _make_user("verified-phone@printy.test", "0700100002")
        set_profile_phone(user, "0700100002", verified=True)

        response = self._login("0700100002")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["id"], user.id)

    def test_unverified_phone_with_unverified_email_cannot_log_in(self):
        _make_user("unverified-phone-email@printy.test", "0700100003", email_verified=False)

        response = self._login("0700100003")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("No active account", str(response.data))

    def test_unverified_phone_link_still_requires_the_right_password(self):
        _make_user("wrong-password@printy.test", "0700100004", email_verified=True)

        response = self._login("0700100004", password="not-the-password")

        self.assertEqual(response.status_code, 400, response.data)

    def test_ambiguous_unverified_phones_are_rejected(self):
        _make_user("ambiguous-a@printy.test", "0700100005", email_verified=True)
        _make_user("ambiguous-b@printy.test", "0700100005", email_verified=True)

        with self.assertLogs("api.auth", level="WARNING") as logs:
            response = self._login("0700100005")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(
            any("login_phone_collision" in line for line in logs.output),
            logs.output,
        )


@override_settings(ACCOUNT_EMAIL_VERIFICATION="none")
class PhoneLoginWithoutEmailVerificationTests(TestCase):
    def test_unverified_phone_and_email_can_log_in_when_verification_is_off(self):
        user = _make_user(
            "verification-off@printy.test", "0700100006", email_verified=False
        )

        response = APIClient().post(
            "/api/auth/login/",
            {"phone": "0700100006", "password": PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["id"], user.id)
