from unittest.mock import patch

from allauth.account import app_settings as allauth_app_settings
from allauth.account.models import EmailAddress
from django.core import mail, signing
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import User

CONFIRM_URL = "/api/auth/confirm-email/"
VERIFY_ALIAS_URL = "/api/auth/email/verify/"

MAX_AGE_SECONDS = 60 * 60 * 24 * allauth_app_settings.EMAIL_CONFIRMATION_EXPIRE_DAYS
SALT = allauth_app_settings.SALT
# Deterministic epoch so boundary tests never depend on wall-clock drift.
FIXED_NOW = 1_700_000_000


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://printy.test",
)
class EmailConfirmationStatesTestCase(APITestCase):
    def _make_address(self, email):
        user = User.objects.create_user(
            email=email, password="Pass12345", name="Confirm", role=User.Role.CLIENT
        )
        return EmailAddress.objects.create(
            user=user, email=email, primary=True, verified=False
        )

    def _key_for(self, email_address, *, issued_at=None, salt=None):
        if issued_at is None:
            return signing.dumps(email_address.pk, salt=salt or SALT)
        with patch("time.time", return_value=issued_at):
            return signing.dumps(email_address.pk, salt=salt or SALT)

    def _confirm(self, key, *, now=None):
        if now is None:
            return self.client.post(CONFIRM_URL, {"key": key}, format="json")
        with patch("time.time", return_value=now):
            return self.client.post(CONFIRM_URL, {"key": key}, format="json")

    def test_valid_link_confirms_the_address(self):
        email_address = self._make_address("valid@test.com")
        key = self._key_for(email_address)

        response = self._confirm(key)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["verified"])
        self.assertFalse(response.data["already_verified"])
        email_address.refresh_from_db()
        self.assertTrue(email_address.verified)

    def test_expired_link_is_rejected_as_expired(self):
        email_address = self._make_address("expired@test.com")
        key = self._key_for(email_address, issued_at=FIXED_NOW)

        response = self._confirm(key, now=FIXED_NOW + MAX_AGE_SECONDS + 1)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "TOKEN_EXPIRED")
        email_address.refresh_from_db()
        self.assertFalse(email_address.verified)

    def test_exact_boundary_is_still_valid_then_expires(self):
        valid_address = self._make_address("boundary-open@test.com")
        valid_key = self._key_for(valid_address, issued_at=FIXED_NOW)

        still_valid = self._confirm(valid_key, now=FIXED_NOW + MAX_AGE_SECONDS)
        self.assertEqual(still_valid.status_code, 200)

        expired_address = self._make_address("boundary-closed@test.com")
        expired_key = self._key_for(expired_address, issued_at=FIXED_NOW)

        expired = self._confirm(expired_key, now=FIXED_NOW + MAX_AGE_SECONDS + 1)
        self.assertEqual(expired.status_code, 400)
        self.assertEqual(expired.data["code"], "TOKEN_EXPIRED")

    def test_malformed_and_tampered_keys_are_invalid(self):
        email_address = self._make_address("tampered@test.com")
        key = self._key_for(email_address)
        flipped = key[:-1] + ("A" if key[-1] != "A" else "B")

        for bad_key in ("", "not-a-real-key", flipped):
            with self.subTest(key=bad_key):
                response = self.client.post(CONFIRM_URL, {"key": bad_key}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(response.data["code"], {"TOKEN_INVALID", "TOKEN_REQUIRED"})

        email_address.refresh_from_db()
        self.assertFalse(email_address.verified)

    def test_key_for_unknown_address_or_wrong_purpose_is_invalid(self):
        email_address = self._make_address("purpose@test.com")

        unknown = signing.dumps(9_999_999, salt=allauth_app_settings.SALT)
        wrong_purpose = signing.dumps(email_address.pk, salt="password-reset")

        for bad_key in (unknown, wrong_purpose):
            with self.subTest(key=bad_key):
                response = self.client.post(CONFIRM_URL, {"key": bad_key}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], "TOKEN_INVALID")

        email_address.refresh_from_db()
        self.assertFalse(email_address.verified)

    def test_key_only_confirms_its_own_address(self):
        target = self._make_address("target@test.com")
        other = self._make_address("other@test.com")

        response = self._confirm(self._key_for(target))

        self.assertEqual(response.status_code, 200)
        target.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(target.verified)
        self.assertFalse(other.verified)

    def test_reusing_a_valid_key_for_a_verified_address_is_idempotent(self):
        email_address = self._make_address("reuse@test.com")
        key = self._key_for(email_address)

        first = self._confirm(key)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.data["already_verified"])

        second = self._confirm(key)

        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["verified"])
        self.assertTrue(second.data["already_verified"])
        email_address.refresh_from_db()
        self.assertTrue(email_address.verified)

    def test_reading_the_endpoint_does_not_consume_the_token(self):
        email_address = self._make_address("scan@test.com")
        key = self._key_for(email_address)

        # A scanner or link preview that only issues a GET must not verify.
        for url in (CONFIRM_URL, VERIFY_ALIAS_URL):
            with self.subTest(url=url):
                get_response = self.client.get(url)
                self.assertEqual(get_response.status_code, 405)
                email_address.refresh_from_db()
                self.assertFalse(email_address.verified)

        later = self._confirm(key)
        self.assertEqual(later.status_code, 200)

    def test_emailed_url_key_validates_end_to_end(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "roundtrip@test.com",
                "password": "Pass12345",
                "name": "Round Trip",
                "role": "client",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)

        self.assertIn("printy.test/auth/confirm-email?key=", mail.outbox[0].body)
        link = next(token for token in mail.outbox[0].body.split() if "key=" in token)
        from urllib.parse import parse_qs, urlparse

        key = parse_qs(urlparse(link.strip()).query)["key"][0]

        confirm = self.client.post(CONFIRM_URL, {"key": key}, format="json")
        self.assertEqual(confirm.status_code, 200)
        self.assertTrue(
            EmailAddress.objects.get(email="roundtrip@test.com").verified
        )
