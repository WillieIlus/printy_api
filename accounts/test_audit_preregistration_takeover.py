"""Audit #4b: pre-registration account takeover.

The attack: someone registers a local account for an address they do not own
(``victim@example.com``, password known to them). The account stays
unconfirmed. When the real owner later signs in with Google, the old code
matched the account by email, marked the address verified and linked Google,
which turned the registrant's password (and any tokens) into a working login
to the owner's account.
"""
from __future__ import annotations

from unittest import mock

from allauth.account.forms import default_token_generator
from allauth.account.models import EmailAddress
from allauth.account.utils import user_pk_to_url_str
from django.contrib.auth import get_user
from django.core import mail
from django.http import HttpRequest
from django.test import Client, TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User, UserProfile
from accounts.test_audit_roles_oauth_tenancy import CLIENT_ID, KID, _CERT_PEM, google_token

VICTIM = "victim@example.com"
ATTACKER_PASSWORD = "Attacker-Pass-123"
GOOGLE_URL = "/api/auth/social/google/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/token/refresh/"


def _login(email=None, password=ATTACKER_PASSWORD, phone=None):
    payload = {"password": password}
    if email:
        payload["email"] = email
    if phone:
        payload["phone"] = phone
    return APIClient().post(LOGIN_URL, payload, format="json")


def _session_user(client: Client):
    request = HttpRequest()
    request.session = client.session
    return get_user(request)


@override_settings(
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    GOOGLE_CLIENT_IDS=[CLIENT_ID],
    GOOGLE_OAUTH_DEV_SKIP_AUDIENCE=False,
)
class _Base(TestCase):
    def setUp(self):
        patcher = mock.patch("google.oauth2.id_token._fetch_certs", return_value={KID: _CERT_PEM})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _register_as_attacker(self, email=VICTIM, phone=""):
        payload = {"email": email, "password": ATTACKER_PASSWORD, "name": "Not The Owner"}
        if phone:
            payload["phone"] = phone
        response = APIClient().post("/api/auth/register/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return User.objects.get(email__iexact=email)

    def _google(self, email=VICTIM, sub="victim-google-sub"):
        return APIClient().post(GOOGLE_URL, {"id_token": google_token(email=email, sub=sub)}, format="json")


class EmailConfirmationTodayTests(_Base):
    """How confirmation works before this commit (documented, unchanged)."""

    def test_registration_creates_an_unverified_primary_address_and_sends_a_link(self):
        user = self._register_as_attacker()
        address = EmailAddress.objects.get(user=user)
        self.assertTrue(address.primary)
        self.assertFalse(address.verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [VICTIM])
        self.assertIn("/auth/confirm-email?key=", mail.outbox[0].body)

    def test_password_login_is_refused_before_confirmation(self):
        self._register_as_attacker()
        response = _login(VICTIM)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("EMAIL_UNVERIFIED", str(response.content))
        self.assertNotIn("access", response.json())

    def test_phone_login_is_refused_before_confirmation(self):
        self._register_as_attacker(phone="254711000111")
        self.assertTrue(UserProfile.objects.filter(phone_normalized="254711000111").exists())
        response = _login(phone="254711000111")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertNotIn("access", response.json())

    def test_registering_the_same_address_again_in_another_case_is_refused(self):
        self._register_as_attacker()
        response = APIClient().post(
            "/api/auth/register/", {"email": "Victim@Example.com", "password": "Owner-Pass-123"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(User.objects.filter(email__iexact=VICTIM).count(), 1)


class GoogleLoginOnUnconfirmedAccountTests(_Base):
    def test_registrants_password_stops_working_and_address_is_confirmed(self):
        squatted = self._register_as_attacker()
        response = self._google()
        self.assertEqual(response.status_code, 200, response.content)
        # Same account (no second account, nothing merged elsewhere).
        self.assertEqual(response.json()["user"]["id"], squatted.pk)
        self.assertEqual(User.objects.filter(email__iexact=VICTIM).count(), 1)
        squatted.refresh_from_db()
        self.assertFalse(squatted.has_usable_password())
        self.assertTrue(EmailAddress.objects.get(user=squatted, email__iexact=VICTIM).verified)
        # The registrant's password does not log in now that the address is confirmed.
        attacker = _login(VICTIM)
        self.assertEqual(attacker.status_code, 400, attacker.content)
        self.assertNotIn("access", attacker.json())

    def test_refresh_tokens_issued_before_are_revoked(self):
        squatted = self._register_as_attacker()
        stolen = str(RefreshToken.for_user(squatted))
        self.assertEqual(APIClient().post(REFRESH_URL, {"refresh": stolen}, format="json").status_code, 200)
        squatted = User.objects.get(pk=squatted.pk)
        stolen = str(RefreshToken.for_user(squatted))
        self.assertEqual(self._google().status_code, 200)
        response = APIClient().post(REFRESH_URL, {"refresh": stolen}, format="json")
        self.assertEqual(response.status_code, 401, response.content)

    def test_the_owners_new_tokens_work(self):
        self._register_as_attacker()
        response = self._google()
        refreshed = APIClient().post(REFRESH_URL, {"refresh": response.json()["refresh"]}, format="json")
        self.assertEqual(refreshed.status_code, 200, refreshed.content)

    def test_django_sessions_from_before_stop_authenticating(self):
        squatted = self._register_as_attacker()
        browser = Client()
        browser.force_login(squatted)
        self.assertEqual(_session_user(browser).pk, squatted.pk)
        self.assertEqual(self._google().status_code, 200)
        self.assertFalse(_session_user(browser).is_authenticated)

    def test_phone_first_account_that_attached_the_address_is_secured(self):
        # A phone-first account adds an address it does not own; it is never
        # verified, so the Google owner must not inherit the account as-is.
        squatter = User.objects.create_user(
            phone="254722000333", phone_verified=True, password=ATTACKER_PASSWORD, role=User.Role.CLIENT
        )
        api = APIClient()
        api.force_authenticate(squatter)
        self.assertEqual(api.patch("/api/auth/me/", {"email": VICTIM}, format="json").status_code, 200)
        self.assertEqual(self._google().status_code, 200)
        squatter.refresh_from_db()
        self.assertFalse(squatter.has_usable_password())
        self.assertNotIn("access", _login(phone="254722000333").json())

    def test_verified_account_keeps_its_password(self):
        owner = User.objects.create_user(email=VICTIM, password="Owner-Pass-123")
        EmailAddress.objects.create(user=owner, email=VICTIM, primary=True, verified=True)
        token = str(RefreshToken.for_user(owner))
        self.assertEqual(self._google().status_code, 200)
        owner.refresh_from_db()
        self.assertTrue(owner.check_password("Owner-Pass-123"))
        self.assertEqual(APIClient().post(REFRESH_URL, {"refresh": token}, format="json").status_code, 200)

    def test_legacy_account_without_address_rows_keeps_its_password(self):
        # Same trust rule password login applies (see results.txt finding).
        legacy = User.objects.create_user(email=VICTIM, password="Owner-Pass-123")
        self.assertEqual(self._google().status_code, 200)
        legacy.refresh_from_db()
        self.assertTrue(legacy.check_password("Owner-Pass-123"))


class PasswordResetDoesNotTakeOverTests(_Base):
    def _reset_key(self, user):
        return f"{user_pk_to_url_str(user)}-{default_token_generator.make_token(user)}"

    def test_reset_link_goes_only_to_the_address(self):
        self._register_as_attacker()
        mail.outbox.clear()
        response = APIClient().post("/api/auth/password-reset/", {"email": VICTIM}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([m.to for m in mail.outbox], [[VICTIM]])

    def test_reset_replaces_the_registrants_password_and_revokes_their_tokens(self):
        squatted = self._register_as_attacker()
        stolen = str(RefreshToken.for_user(squatted))
        response = APIClient().post(
            "/api/auth/password-reset/confirm/",
            {"key": self._reset_key(squatted), "password": "Owner-New-Pass-456"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        squatted.refresh_from_db()
        self.assertFalse(squatted.check_password(ATTACKER_PASSWORD))
        self.assertEqual(APIClient().post(REFRESH_URL, {"refresh": stolen}, format="json").status_code, 401)
        # A reset does not confirm the address on its own; login still waits
        # for the confirmation link (which only the address owner receives).
        self.assertIn("EMAIL_UNVERIFIED", str(_login(VICTIM, "Owner-New-Pass-456").content))


class AttachEmailToPhoneAccountTests(_Base):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(email=VICTIM, password="Owner-Pass-123")
        self.phone_user = User.objects.create_user(phone="254733000444", phone_verified=True, password="x-Pass-123")
        self.api = APIClient()
        self.api.force_authenticate(self.phone_user)

    def test_case_variant_of_an_existing_address_is_refused(self):
        response = self.api.patch("/api/auth/me/", {"email": "VICTIM@example.com"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(User.objects.filter(email__iexact=VICTIM).count(), 1)

    def test_exact_duplicate_is_a_400_not_a_500(self):
        response = self.api.patch("/api/auth/me/", {"email": VICTIM}, format="json")
        self.assertEqual(response.status_code, 400, response.content)

    def test_unused_address_can_still_be_added(self):
        response = self.api.patch("/api/auth/me/", {"email": "phone-owner@example.com"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.phone_user.refresh_from_db()
        self.assertEqual(self.phone_user.email, "phone-owner@example.com")
