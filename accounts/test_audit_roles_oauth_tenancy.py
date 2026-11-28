"""Audit #3 / #4 / #5 regression tests.

#3 privilege escalation: self-service role/capability writes, sign-up
   allowlist, manager verification on the marketplace gate, canonical phone
   writes.
#4 Google OAuth: local ID-token verification (aud/iss/signature), fail closed
   without GOOGLE_CLIENT_ID, email_verified, case-folded lookup, inactive users,
   role only on creation; scoped throttles on credential endpoints.
#5 ProductionOrder tenancy: shop resolved server-side, quote ownership, no
   moving orders, tenant-scoped list/retrieve.

No real credentials: Google ID tokens are signed with a throwaway RSA key and
google-auth's cert fetch is patched to return the matching certificate, so the
real verify_oauth2_token code path (signature, expiry, iss, aud) runs.
"""
from __future__ import annotations

import datetime
import time
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from shops.models import Shop


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _user(email, *, role=User.Role.CLIENT, **extra):
    return User.objects.create_user(email=email, password="Pass12345!", role=role, **extra)


def _manager(email, *, verified, active=True):
    user = _user(email, role=User.Role.PARTNER, partner_profile_enabled=True)
    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            "manager_verified": verified,
            "manager_verified_at": timezone.now() if verified else None,
            "broker_profile_active": active,
        },
    )
    return user


def _shop(owner, name):
    return Shop.objects.create(owner=owner, name=name, is_active=True, is_public=True)


def _auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# #3 — self-service privilege fields
# ---------------------------------------------------------------------------


class SelfServicePrivilegeFieldTests(TestCase):
    def setUp(self):
        self.user = _user("plain-client@test.ke")

    def _assert_unchanged(self):
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.CLIENT)
        self.assertFalse(self.user.partner_profile_enabled)
        self.assertEqual(self.user.capability_overrides or {}, {})
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)

    def test_patch_auth_me_cannot_change_role_capabilities_or_partner_flag(self):
        for role in ("super_admin", "partner", "production"):
            response = _auth_client(self.user).patch(
                "/api/auth/me/",
                {
                    "role": role,
                    "partner_profile_enabled": True,
                    "capability_overrides": {"can_manage_clients": True, "can_source_jobs": True},
                    "name": "Renamed",
                },
                format="json",
            )
            self.assertIn(response.status_code, (200, 400), response.content)
            self._assert_unchanged()
        self.assertEqual(self.user.name, "Renamed")

    def test_patch_users_me_alias_cannot_change_role_capabilities_or_partner_flag(self):
        response = _auth_client(self.user).patch(
            "/api/users/me/",
            {
                "role": "partner",
                "partner_profile_enabled": True,
                "capability_overrides": {"can_source_jobs": True},
            },
            format="json",
        )
        self.assertIn(response.status_code, (200, 400), response.content)
        self._assert_unchanged()
        self.assertNotIn("partner", response.json().get("roles", []))


class RegistrationRoleAllowlistTests(TestCase):
    def _register(self, path="/api/auth/register/", **payload):
        body = {"email": payload.pop("email", "new-user@test.ke"), "password": "Pass12345!", **payload}
        return APIClient().post(path, body, format="json")

    def test_super_admin_and_production_are_rejected_on_every_signup_route(self):
        paths = (
            "/api/auth/register/",
            "/api/auth/register/client/",
            "/api/auth/register/partner/",
            "/api/auth/register/production/",
        )
        for index, path in enumerate(paths):
            for role in ("super_admin", "admin", "staff", "production", "printer", "shop_owner"):
                email = f"reg-{index}-{role}@test.ke"
                response = self._register(path, email=email, role=role)
                self.assertEqual(response.status_code, 400, (path, role, response.content))
                self.assertFalse(User.objects.filter(email=email).exists(), (path, role))

    def test_signup_ignores_partner_flag_and_capability_overrides(self):
        response = self._register(
            email="sneaky@test.ke",
            role="client",
            partner_profile_enabled=True,
            capability_overrides={"can_manage_clients": True},
        )
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(email="sneaky@test.ke")
        self.assertFalse(user.partner_profile_enabled)
        self.assertEqual(user.capability_overrides or {}, {})
        self.assertEqual(user.role, User.Role.CLIENT)

    def test_printer_signup_is_an_applicant_without_production_or_shop(self):
        response = self._register(
            "/api/auth/register/production/", email="printer-app@test.ke", shop_name="Applicant Press"
        )
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(email="printer-app@test.ke")
        self.assertEqual(user.role, User.Role.CLIENT)
        self.assertFalse(Shop.objects.filter(owner=user).exists())
        from accounts.services.roles import resolve_user_roles

        self.assertNotIn("production", resolve_user_roles(user))

    def test_manager_signup_starts_unverified_and_is_refused_by_the_marketplace(self):
        response = self._register("/api/auth/register/partner/", email="mgr-app@test.ke")
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(email="mgr-app@test.ke")
        self.assertEqual(user.role, User.Role.PARTNER)
        self.assertFalse(user.profile.manager_verified)
        self.assertEqual(_auth_client(user).get("/api/public/shops/").status_code, 403)


# ---------------------------------------------------------------------------
# #3 — marketplace gate enforces manager verification
# ---------------------------------------------------------------------------


class MarketplaceManagerVerificationTests(TestCase):
    def setUp(self):
        self.printer = _user("gate-printer@test.ke", role=User.Role.PRODUCTION)
        self.shop = _shop(self.printer, "Gate Press")
        self.other_printer = _user("gate-printer-2@test.ke", role=User.Role.PRODUCTION)
        self.other_shop = _shop(self.other_printer, "Other Gate Press")

    def _status(self, user, url):
        return _auth_client(user).get(url).status_code

    def _urls(self, shop=None):
        shop = shop or self.shop
        return ("/api/public/shops/", f"/api/shops/{shop.slug}/rate-card/")

    def test_pending_manager_is_refused(self):
        manager = _manager("pending-mgr@test.ke", verified=False, active=True)
        for url in self._urls():
            self.assertEqual(self._status(manager, url), 403, url)

    def test_rejected_manager_is_refused(self):
        manager = _manager("rejected-mgr@test.ke", verified=False, active=False)
        for url in self._urls():
            self.assertEqual(self._status(manager, url), 403, url)

    def test_verified_but_suspended_manager_is_refused(self):
        manager = _manager("suspended-mgr@test.ke", verified=True, active=False)
        for url in self._urls():
            self.assertEqual(self._status(manager, url), 403, url)

    def test_verified_active_manager_is_allowed(self):
        manager = _manager("verified-mgr@test.ke", verified=True, active=True)
        for url in self._urls():
            self.assertEqual(self._status(manager, url), 200, url)

    def test_rejected_manager_cannot_regain_access_by_owning_a_shop(self):
        manager = _manager("rejected-shop-mgr@test.ke", verified=False, active=False)
        own = _shop(manager, "Loophole Press")
        self.assertEqual(self._status(manager, "/api/public/shops/"), 403)
        self.assertEqual(self._status(manager, f"/api/shops/{self.other_shop.slug}/rate-card/"), 403)
        # Their own shop's rate card is still theirs.
        self.assertEqual(self._status(manager, f"/api/shops/{own.slug}/rate-card/"), 200)

    def test_printer_sees_only_its_own_rate_card(self):
        self.assertEqual(self._status(self.printer, f"/api/shops/{self.shop.slug}/rate-card/"), 200)
        self.assertEqual(self._status(self.printer, f"/api/shops/{self.other_shop.slug}/rate-card/"), 403)
        self.assertEqual(self._status(self.printer, "/api/public/shops/"), 403)

    def test_client_is_refused_and_super_admin_allowed(self):
        client = _user("gate-client@test.ke")
        admin = User.objects.create_superuser(email="gate-admin@test.ke", password="Pass12345!")
        for url in self._urls():
            self.assertEqual(self._status(client, url), 403, url)
            self.assertEqual(self._status(admin, url), 200, url)


# ---------------------------------------------------------------------------
# #3 — phone writes go through set_profile_phone
# ---------------------------------------------------------------------------


class CanonicalPhoneWriteTests(TestCase):
    def setUp(self):
        self.user = _user("phone-user@test.ke")
        from accounts.services.phone import set_profile_phone

        set_profile_phone(self.user, "0712345678", verified=True)
        self.profile = UserProfile.objects.get(user=self.user)
        self.assertIsNotNone(self.profile.phone_verified_at)

    def test_patch_me_phone_normalises_and_resets_verification(self):
        from accounts.services import phone as phone_service

        with mock.patch.object(
            phone_service, "set_profile_phone", wraps=phone_service.set_profile_phone
        ) as spy:
            response = _auth_client(self.user).patch("/api/auth/me/", {"phone": "0799 000 111"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(spy.called)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone_normalized, "254799000111")
        self.assertIsNone(self.profile.phone_verified_at)

    def test_resaving_the_same_number_keeps_verification(self):
        response = _auth_client(self.user).patch("/api/auth/me/", {"phone": "+254712345678"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone_normalized, "254712345678")
        self.assertIsNotNone(self.profile.phone_verified_at)

    def test_invalid_phone_is_a_400_and_nothing_changes(self):
        response = _auth_client(self.user).patch("/api/auth/me/", {"phone": "not-a-phone"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone_normalized, "254712345678")
        self.assertIsNotNone(self.profile.phone_verified_at)

    def test_manager_profile_phone_goes_through_the_canonical_writer(self):
        response = _auth_client(self.user).patch(
            "/api/profiles/me/manager-profile/", {"phone": "0711 222 333"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone_normalized, "254711222333")
        self.assertIsNone(self.profile.phone_verified_at)

    def test_payment_save_phone_helpers_use_the_canonical_writer(self):
        from django.test import RequestFactory

        from api import payment_views
        from mpesa_payments import views as mpesa_views

        request = RequestFactory().post("/")
        request.user = self.user
        request.data = {"save_phone_to_profile": True}
        payment_views._save_phone_to_profile(request, "254700111222")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone_normalized, "254700111222")
        self.assertIsNone(self.profile.phone_verified_at)

        mpesa_views._save_profile_phone(request, "254700333444")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone_normalized, "254700333444")


# ---------------------------------------------------------------------------
# #4 — Google OAuth
# ---------------------------------------------------------------------------

CLIENT_ID = "printy-test-client.apps.googleusercontent.com"
KID = "printy-test-kid"


def _make_keypair():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "printy-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return key_pem, cert_pem


_KEY_PEM, _CERT_PEM = _make_keypair()
_OTHER_KEY_PEM, _ = _make_keypair()


def google_token(*, key_pem=_KEY_PEM, **claims):
    from google.auth import crypt, jwt

    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "person@example.com",
        "email_verified": True,
        "name": "Person Example",
        "iat": now,
        "exp": now + 600,
    }
    payload.update(claims)
    signer = crypt.RSASigner.from_string(key_pem, key_id=KID)
    return jwt.encode(signer, payload).decode()


@override_settings(GOOGLE_CLIENT_IDS=[CLIENT_ID], GOOGLE_OAUTH_DEV_SKIP_AUDIENCE=False)
class GoogleLoginTests(TestCase):
    url = "/api/auth/social/google/"

    def setUp(self):
        patcher = mock.patch("google.oauth2.id_token._fetch_certs", return_value={KID: _CERT_PEM})
        self.fetch_certs = patcher.start()
        self.addCleanup(patcher.stop)
        # The old implementation called Google's tokeninfo endpoint; make sure
        # nothing reaches the network.
        net = mock.patch("requests.get", side_effect=AssertionError("network call"))
        net.start()
        self.addCleanup(net.stop)

    def _post(self, token, **extra):
        return APIClient().post(self.url, {"id_token": token, **extra}, format="json")

    def test_valid_token_creates_a_client_account(self):
        response = self._post(google_token())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access", response.json())
        user = User.objects.get(email="person@example.com")
        self.assertEqual(user.role, User.Role.CLIENT)
        self.assertFalse(user.has_usable_password())

    def test_wrong_audience_is_rejected(self):
        response = self._post(google_token(aud="someone-else.apps.googleusercontent.com"))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(User.objects.filter(email="person@example.com").exists())

    def test_wrong_issuer_is_rejected(self):
        response = self._post(google_token(iss="https://evil.example.com"))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(User.objects.filter(email="person@example.com").exists())

    def test_bad_signature_is_rejected(self):
        response = self._post(google_token(key_pem=_OTHER_KEY_PEM))
        self.assertEqual(response.status_code, 400, response.content)

    def test_expired_token_is_rejected(self):
        past = int(time.time()) - 3600
        response = self._post(google_token(iat=past - 600, exp=past))
        self.assertEqual(response.status_code, 400, response.content)

    @override_settings(GOOGLE_CLIENT_IDS=[])
    def test_missing_client_id_fails_closed(self):
        response = self._post(google_token())
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json().get("code"), "google_oauth_not_configured")
        self.assertFalse(User.objects.filter(email="person@example.com").exists())

    @override_settings(GOOGLE_CLIENT_IDS=[], GOOGLE_OAUTH_DEV_SKIP_AUDIENCE=True, DEBUG=False, APP_ENV="production")
    def test_dev_override_is_ignored_outside_debug(self):
        response = self._post(google_token(aud="anything"))
        self.assertEqual(response.status_code, 503, response.content)

    @override_settings(GOOGLE_CLIENT_IDS=[], GOOGLE_OAUTH_DEV_SKIP_AUDIENCE=True, DEBUG=True, APP_ENV="local")
    def test_explicit_dev_override_skips_only_the_audience(self):
        self.assertEqual(self._post(google_token(aud="anything")).status_code, 200)
        # Issuer and signature are still enforced under the override.
        self.assertEqual(self._post(google_token(iss="https://evil.example.com", sub="s2")).status_code, 400)
        self.assertEqual(self._post(google_token(key_pem=_OTHER_KEY_PEM, sub="s3")).status_code, 400)

    def test_unverified_email_is_rejected(self):
        for value in (False, "false"):
            response = self._post(google_token(email_verified=value))
            self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(User.objects.filter(email="person@example.com").exists())

    def test_inactive_user_is_rejected(self):
        user = _user("person@example.com")
        user.is_active = False
        user.save(update_fields=["is_active"])
        response = self._post(google_token())
        self.assertEqual(response.status_code, 403, response.content)
        self.assertNotIn("access", response.json())

    def test_existing_super_admin_role_is_never_touched(self):
        admin = User.objects.create_superuser(email="boss@example.com", password="Pass12345!")
        response = self._post(google_token(email="boss@example.com"), role="client")
        self.assertEqual(response.status_code, 200, response.content)
        admin.refresh_from_db()
        self.assertEqual(admin.role, User.Role.SUPER_ADMIN)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)

    def test_existing_client_cannot_become_partner_via_google(self):
        user = _user("client-g@example.com")
        response = self._post(google_token(email="client-g@example.com"), role="partner", partner_profile_enabled=True)
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertEqual(user.role, User.Role.CLIENT)
        self.assertFalse(user.partner_profile_enabled)

    def test_email_case_resolves_to_one_account(self):
        existing = User.objects.create_user(email="name@example.com", password="Pass12345!")
        # Simulate a legacy row stored with mixed case.
        User.objects.filter(pk=existing.pk).update(email="Name@Example.com")
        response = self._post(google_token(email="name@example.com"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(User.objects.filter(email__iexact="name@example.com").count(), 1)
        self.assertEqual(response.json()["user"]["id"], existing.pk)

    def test_existing_case_duplicates_are_refused_not_merged(self):
        a = User.objects.create_user(email="dup@example.com", password="Pass12345!")
        b = User.objects.create_user(email="dup2@example.com", password="Pass12345!")
        User.objects.filter(pk=b.pk).update(email="DUP@example.com")
        response = self._post(google_token(email="dup@example.com"))
        self.assertEqual(response.status_code, 409, response.content)
        self.assertTrue(User.objects.filter(pk=a.pk).exists())
        self.assertTrue(User.objects.filter(pk=b.pk).exists())

    def test_new_user_role_comes_only_from_the_allowlist(self):
        response = self._post(google_token(email="p1@example.com", sub="p1"), role="production")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(User.objects.get(email="p1@example.com").role, User.Role.CLIENT)

        response = self._post(google_token(email="p2@example.com", sub="p2"), role="super_admin")
        self.assertEqual(response.status_code, 200, response.content)
        p2 = User.objects.get(email="p2@example.com")
        self.assertEqual(p2.role, User.Role.CLIENT)
        self.assertFalse(p2.is_superuser)

        response = self._post(google_token(email="p3@example.com", sub="p3"), role="partner")
        self.assertEqual(response.status_code, 200, response.content)
        p3 = User.objects.get(email="p3@example.com")
        self.assertEqual(p3.role, User.Role.PARTNER)
        self.assertFalse(p3.profile.manager_verified)


class DuplicateEmailReportTests(TestCase):
    def test_command_lists_case_duplicates_without_merging(self):
        from io import StringIO

        from django.core.management.base import CommandError

        a = User.objects.create_user(email="same@example.com", password="x")
        b = User.objects.create_user(email="other@example.com", password="x")
        User.objects.filter(pk=b.pk).update(email="SAME@example.com")
        out = StringIO()
        call_command("report_duplicate_emails", stdout=out)
        text = out.getvalue()
        self.assertIn("1 duplicate email group(s)", text)
        self.assertIn(f"id={a.pk}", text)
        self.assertIn(f"id={b.pk}", text)
        self.assertEqual(User.objects.filter(email__iexact="same@example.com").count(), 2)
        with self.assertRaises(CommandError):
            call_command("report_duplicate_emails", "--fail-if-any", stdout=StringIO())


# ---------------------------------------------------------------------------
# #4 — scoped throttles on credential endpoints
# ---------------------------------------------------------------------------


def _low_rates(**rates):
    base = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    base.update(rates)
    return {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": base}


class AuthThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _hammer(self, url, payload, limit, **extra):
        client = APIClient()
        codes = [client.post(url, payload, format="json", **extra).status_code for _ in range(limit + 1)]
        return codes

    def test_production_rates_are_configured_for_every_auth_scope(self):
        from config import settings as prod_settings

        rates = prod_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        for scope in (
            "auth_login",
            "auth_register",
            "auth_password_reset",
            "auth_password_reset_confirm",
            "auth_google_login",
            "client_otp_request",
            "client_otp_verify",
        ):
            self.assertIn(scope, rates)

    def test_login_is_throttled(self):
        with override_settings(REST_FRAMEWORK=_low_rates(auth_login="2/min")):
            codes = self._hammer("/api/auth/login/", {"email": "x@test.ke", "password": "nope"}, 2)
        self.assertNotIn(429, codes[:2])
        self.assertEqual(codes[-1], 429)

    def test_register_is_throttled(self):
        with override_settings(REST_FRAMEWORK=_low_rates(auth_register="2/min")):
            client = APIClient()
            codes = [
                client.post(
                    "/api/auth/register/", {"email": f"t{i}@test.ke", "password": "Pass12345!"}, format="json"
                ).status_code
                for i in range(3)
            ]
        self.assertEqual(codes[:2], [201, 201])
        self.assertEqual(codes[2], 429)

    def test_password_reset_is_throttled(self):
        with override_settings(REST_FRAMEWORK=_low_rates(auth_password_reset="2/min")):
            codes = self._hammer("/api/auth/password-reset/", {"email": "x@test.ke"}, 2)
        self.assertEqual(codes[-1], 429)

    def test_password_reset_confirm_is_throttled(self):
        with override_settings(REST_FRAMEWORK=_low_rates(auth_password_reset_confirm="2/min")):
            codes = self._hammer("/api/auth/password-reset/confirm/", {"key": "a-b", "password": "x"}, 2)
        self.assertEqual(codes[-1], 429)

    @override_settings(GOOGLE_CLIENT_IDS=[CLIENT_ID])
    def test_google_login_is_throttled(self):
        with override_settings(REST_FRAMEWORK=_low_rates(auth_google_login="2/min")):
            codes = self._hammer("/api/auth/social/google/", {"id_token": ""}, 2)
        self.assertEqual(codes[-1], 429)

    def test_spoofed_x_forwarded_for_does_not_reset_the_bucket(self):
        with override_settings(REST_FRAMEWORK=_low_rates(auth_login="2/min")):
            client = APIClient()
            codes = [
                client.post(
                    "/api/auth/login/",
                    {"email": "x@test.ke", "password": "nope"},
                    format="json",
                    REMOTE_ADDR="203.0.113.9",
                    HTTP_X_FORWARDED_FOR=f"198.51.100.{i}",
                ).status_code
                for i in range(3)
            ]
        self.assertEqual(codes[-1], 429)

    def test_client_otp_request_and_verify_are_throttled_per_real_ip(self):
        from clients.throttling import ClientOtpRequestThrottle, ClientOtpVerifyThrottle

        for throttle, suffix in ((ClientOtpRequestThrottle, "request"), (ClientOtpVerifyThrottle, "verify")):
            cache.clear()
            with mock.patch.object(throttle, "rate", "2/min", create=True):
                client = APIClient()
                codes = [
                    client.post(
                        f"/api/client-portal/links/no-such-token/otp/{suffix}/",
                        {"code": "000000"},
                        format="json",
                        REMOTE_ADDR="203.0.113.10",
                        HTTP_X_FORWARDED_FOR=f"198.51.100.{i}",
                    ).status_code
                    for i in range(3)
                ]
            self.assertNotEqual(codes[0], 429, suffix)
            self.assertEqual(codes[-1], 429, (suffix, codes))


# ---------------------------------------------------------------------------
# #5 — ProductionOrder tenancy
# ---------------------------------------------------------------------------


class ProductionOrderTenancyTests(TestCase):
    url = "/api/production-orders/"

    def setUp(self):
        from production.models import ProductionOrder
        from quotes.models import Quote, QuoteRequest

        self.printer_a = _user("prod-a@test.ke", role=User.Role.PRODUCTION)
        self.printer_b = _user("prod-b@test.ke", role=User.Role.PRODUCTION)
        self.shop_a = _shop(self.printer_a, "Tenant A Press")
        self.shop_b = _shop(self.printer_b, "Tenant B Press")
        buyer = _user("prod-buyer@test.ke")

        def quote_for(shop, owner):
            qr = QuoteRequest.objects.create(
                shop=shop,
                created_by=buyer,
                customer_name="Buyer",
                customer_email="prod-buyer@test.ke",
                status=QuoteRequest.SUBMITTED,
            )
            return Quote.objects.create(
                quote_request=qr, shop=shop, created_by=owner, status=Quote.ACCEPTED, total="1000.00"
            )

        self.quote_a = quote_for(self.shop_a, self.printer_a)
        self.quote_b = quote_for(self.shop_b, self.printer_b)
        self.order_a = ProductionOrder.objects.create(shop=self.shop_a, quote=self.quote_a, title="A job")
        self.order_b = ProductionOrder.objects.create(shop=self.shop_b, quote=self.quote_b, title="B job")

    def test_cannot_create_for_another_shop(self):
        client = _auth_client(self.printer_a)
        response = client.post(self.url, {"shop": self.shop_b.id, "title": "Hijack"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        response = client.post(self.url, {"quote": self.quote_b.id, "title": "Hijack"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(self.shop_b.production_orders.filter(title="Hijack").exists())

    def test_create_resolves_shop_server_side(self):
        client = _auth_client(self.printer_a)
        response = client.post(self.url, {"title": "Walk-in"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["shop"], self.shop_a.id)
        response = client.post(self.url, {"quote": self.quote_a.id, "title": "From quote"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["shop"], self.shop_a.id)

    def test_quote_reassigned_to_another_shop_is_refused(self):
        from jobs.models import JobAssignment, ManagedJob

        job = ManagedJob.objects.create(client=self.quote_a.quote_request.created_by, title="Reassigned")
        JobAssignment.objects.create(managed_job=job, assigned_shop=self.shop_b, source_quote=self.quote_a)
        response = _auth_client(self.printer_a).post(self.url, {"quote": self.quote_a.id}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        response = _auth_client(self.printer_b).post(self.url, {"quote": self.quote_a.id}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["shop"], self.shop_b.id)

    def test_cannot_move_an_order_to_another_shop_or_quote(self):
        client = _auth_client(self.printer_a)
        detail = f"{self.url}{self.order_a.id}/"
        self.assertEqual(client.patch(detail, {"shop": self.shop_b.id}, format="json").status_code, 400)
        self.assertEqual(client.patch(detail, {"quote": self.quote_b.id}, format="json").status_code, 400)
        self.order_a.refresh_from_db()
        self.assertEqual(self.order_a.shop_id, self.shop_a.id)
        self.assertEqual(self.order_a.quote_id, self.quote_a.id)
        ok = client.patch(detail, {"status": "in_progress"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_cannot_list_or_read_another_shops_orders(self):
        client = _auth_client(self.printer_a)
        listing = client.get(self.url)
        self.assertEqual(listing.status_code, 200)
        rows = listing.json()
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        self.assertEqual({row["id"] for row in rows}, {self.order_a.id})
        self.assertEqual(client.get(f"{self.url}{self.order_b.id}/").status_code, 404)
        self.assertEqual(client.patch(f"{self.url}{self.order_b.id}/", {"title": "x"}, format="json").status_code, 404)
        self.assertEqual(client.delete(f"{self.url}{self.order_b.id}/").status_code, 404)

    def test_non_printer_is_refused_and_platform_admin_sees_all(self):
        buyer = _user("prod-nobody@test.ke")
        self.assertEqual(_auth_client(buyer).get(self.url).status_code, 403)
        self.assertEqual(_auth_client(buyer).post(self.url, {"title": "x"}, format="json").status_code, 403)
        admin = User.objects.create_superuser(email="prod-admin@test.ke", password="Pass12345!")
        listing = _auth_client(admin).get(self.url).json()
        rows = listing.get("results", listing) if isinstance(listing, dict) else listing
        self.assertEqual({row["id"] for row in rows}, {self.order_a.id, self.order_b.id})
