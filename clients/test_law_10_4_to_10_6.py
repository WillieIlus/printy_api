"""Law 10.4-10.6 — phone-first clients, managed onboarding and single-job links.

Covers prompt items:

* 10.4 one phone normalisation utility; unique/reused client by phone; email
  optional; OTP stored hashed, expiring, attempt-limited, resend-throttled and
  required before artwork upload, acknowledgements and payment initiation.
* 10.5 a manager may create a quote request on behalf of a client (their only
  buyer power) and sees only the clients they onboarded or have jobs with.
* 10.6 a signed single-job link: revocable, rate-limited, every use logged,
  new link replaces old, buyer projection only.
* 10.6 provider-agnostic messaging with a logging stub and an honoured opt-out.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services.phone import normalize_phone
from clients.models import (
    ClientContactPreference,
    ClientLink,
    ClientLinkUse,
    MessageDelivery,
)
from clients.services.messaging import build_client_message_body
from clients.throttling import ClientLinkViewThrottle, ClientOtpRequestThrottle
from clients.services.otp import request_otp, verify_otp
from jobs.job_codes import quote_request_code
from quotes.models import QuoteRequest

MANAGER_CREATE_URL = "/api/client-portal/manager/quote-requests/"
PARTNER_CLIENTS_URL = "/api/dashboard/partner/clients/"


def link_url(token, suffix=""):
    return f"/api/client-portal/links/{token}/{suffix}"


def quote_request_from(response) -> QuoteRequest:
    return QuoteRequest.objects.get(pk=response.data["quote_request_id"])


class ClientFlowBase(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.manager = User.objects.create_user(
            email="manager1@example.com", password="pw12345678", name="Manager One", role=User.Role.PARTNER
        )
        self.manager2 = User.objects.create_user(
            email="manager2@example.com", password="pw12345678", name="Manager Two", role=User.Role.PARTNER
        )
        self.printer = User.objects.create_user(
            email="printer@example.com", password="pw12345678", role=User.Role.PRODUCTION
        )
        self.client_user = User.objects.create_user(
            email="client1@example.com",
            password="pw12345678",
            name="Client One",
            role=User.Role.CLIENT,
            phone="0700000001",
            phone_verified=True,
        )

    def api(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def create_request(self, manager=None, phone="0700000001", name="Client One", **extra):
        payload = {"name": name, "phone": phone, "contact_permitted": True}
        payload.update(extra)
        return self.api(manager or self.manager).post(MANAGER_CREATE_URL, payload, format="json")

    def request_obj(self, manager=None, phone="0700000001", name="Client One", **extra):
        return quote_request_from(self.create_request(manager=manager, phone=phone, name=name, **extra))

    def create_link(self, quote_request, manager=None):
        return self.api(manager or self.manager).post(
            f"{MANAGER_CREATE_URL}{quote_request.id}/link/", {}, format="json"
        )


# ---------------------------------------------------------------------------
# Law 10.4 — phone is the identity; OTP
# ---------------------------------------------------------------------------


class PhoneIdentityTests(ClientFlowBase):
    def test_phone_shapes_normalise_to_one_identity(self):
        self.assertEqual(normalize_phone("0700000001"), "254700000001")
        self.assertEqual(normalize_phone("0700000001"), normalize_phone("700000001"))
        self.assertEqual(normalize_phone("0700000001"), normalize_phone("254700000001"))
        self.assertEqual(normalize_phone("0700000001"), normalize_phone("+254700000001"))
        self.assertEqual(normalize_phone("0110000001"), normalize_phone("254110000001"))

    def test_creating_with_existing_number_reuses_the_client(self):
        first = self.create_request(self.manager)
        self.assertEqual(first.status_code, 201, first.content)
        second = self.create_request(self.manager2)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(first.data["client_id"], second.data["client_id"])
        self.assertEqual(User.objects.filter(profile__phone_normalized="254700000001").count(), 1)

    def test_email_is_optional(self):
        resp = self.create_request(self.manager, name="No Email")
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_contact_permission_is_required(self):
        resp = self.api(self.manager).post(
            MANAGER_CREATE_URL,
            {"name": "X", "phone": "0700000001", "contact_permitted": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)


class OtpServiceTests(ClientFlowBase):
    def test_code_is_stored_hashed(self):
        challenge, code = request_otp(phone_normalized="254700000001")
        self.assertNotEqual(challenge.code_hash, code)
        self.assertNotIn(code, challenge.code_hash)
        self.assertTrue(check_password(code, challenge.code_hash))

    def test_code_expires(self):
        challenge, code = request_otp(phone_normalized="254700000001")
        challenge.expires_at = timezone.now() - timedelta(minutes=1)
        challenge.save(update_fields=["expires_at", "updated_at"])
        with self.assertRaises(Exception):
            verify_otp(phone_normalized="254700000001", code=code)

    def test_attempts_are_limited(self):
        challenge, _ = request_otp(phone_normalized="254700000001")
        for _ in range(challenge.max_attempts):
            with self.assertRaises(Exception):
                verify_otp(phone_normalized="254700000001", code="000000")
        challenge.refresh_from_db()
        self.assertTrue(challenge.is_locked)

    def test_resends_are_throttled(self):
        request_otp(phone_normalized="254700000001")
        with self.assertRaises(Exception):
            request_otp(phone_normalized="254700000001")


@override_settings(CLIENT_OTP_ECHO=True)
class OtpGateTests(ClientFlowBase):
    def test_actions_require_a_verified_phone(self):
        quote_request = quote_request_from(self.create_request(self.manager))
        token = self.create_link(quote_request).data["token"]

        self.assertEqual(self.api().get(link_url(token)).status_code, 200)
        self.assertEqual(self.api().post(link_url(token, "artwork/"), {}, format="json").status_code, 403)
        self.assertEqual(
            self.api().post(link_url(token, "acknowledge/"), {"acknowledge": "both"}, format="json").status_code, 403
        )
        self.assertEqual(self.api().post(link_url(token, "payment/"), {}, format="json").status_code, 403)

    def test_verified_phone_unlocks_actions(self):
        quote_request = quote_request_from(self.create_request(self.manager))
        token = self.create_link(quote_request).data["token"]

        otp = self.api().post(link_url(token, "otp/request/"), {}, format="json")
        self.assertEqual(otp.status_code, 200, otp.content)
        verify = self.api().post(link_url(token, "otp/verify/"), {"code": otp.data["code"]}, format="json")
        self.assertEqual(verify.status_code, 200, verify.content)
        headers = {"HTTP_X_PRINTY_OTP": verify.data["otp_token"]}

        artwork = self.api().post(link_url(token, "artwork/"), {}, format="json", **headers)
        self.assertEqual(artwork.status_code, 202, artwork.content)
        ack = self.api().post(link_url(token, "acknowledge/"), {"acknowledge": "both"}, format="json", **headers)
        self.assertEqual(ack.status_code, 200, ack.content)
        quote_request.refresh_from_db()
        self.assertIn("price_acknowledged_at", quote_request.request_snapshot)

    def test_otp_resend_is_rate_limited(self):
        quote_request = quote_request_from(self.create_request(self.manager))
        token = self.create_link(quote_request).data["token"]
        with override_settings(OTP_RESEND_COOLDOWN_SECONDS=0):
            with patch.object(ClientOtpRequestThrottle, "get_rate", return_value="1/hour"):
                first = self.api().post(link_url(token, "otp/request/"), {}, format="json")
                second = self.api().post(link_url(token, "otp/request/"), {}, format="json")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 429, second.content)


# ---------------------------------------------------------------------------
# Law 10.5 — managed onboarding
# ---------------------------------------------------------------------------


class ManagedOnboardingTests(ClientFlowBase):
    def test_manager_creates_request_on_behalf_of_client(self):
        resp = self.create_request(self.manager)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.data["on_behalf_of"], self.client_user.id)
        self.assertEqual(resp.data["created_by_manager"], self.manager.id)
        quote_request = quote_request_from(resp)
        self.assertTrue(quote_request.request_snapshot["client_contact_permitted"])
        events = [e["event"] for e in quote_request.request_snapshot["client_event_log"]]
        self.assertIn("manager_created_client_request", events)

    def test_printer_cannot_create_for_client(self):
        resp = self.api(self.printer).post(
            MANAGER_CREATE_URL,
            {"name": "X", "phone": "0700000001", "contact_permitted": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_manager_still_blocked_from_buyer_only_quote_create(self):
        resp = self.api(self.manager).post("/api/quote-requests/", {"customer_name": "X"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_manager_sees_only_own_clients(self):
        other = User.objects.create_user(
            email="client2@example.com", password="pw12345678", name="Client Two",
            role=User.Role.CLIENT, phone="0700000002", phone_verified=True,
        )
        self.create_request(self.manager, phone="0700000002", name="Client Two")

        mine = self.api(self.manager).get(PARTNER_CLIENTS_URL)
        theirs = self.api(self.manager2).get(PARTNER_CLIENTS_URL)
        mine_ids = {row.get("client_id") for row in mine.data["results"]}
        theirs_ids = {row.get("client_id") for row in theirs.data["results"]}
        self.assertIn(other.id, mine_ids)
        self.assertNotIn(other.id, theirs_ids)


# ---------------------------------------------------------------------------
# Law 10.6 — single-job link
# ---------------------------------------------------------------------------


class ClientLinkTests(ClientFlowBase):
    def _pair(self):
        quote_request = quote_request_from(self.create_request(self.manager))
        token = self.create_link(quote_request).data["token"]
        return quote_request, token

    def test_link_exposes_only_its_own_job(self):
        quote_request, token = self._pair()
        self.create_request(self.manager2, phone="0700000009", name="Other")
        resp = self.api().get(link_url(token))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["code"], quote_request_code(quote_request))
        self.assertNotIn("shop", resp.data)
        self.assertNotIn("printer", resp.data)
        self.assertNotIn("Other", str(resp.data))

    def test_link_is_rate_limited(self):
        _, token = self._pair()
        with patch.object(ClientLinkViewThrottle, "get_rate", return_value="1/hour"):
            first = self.api().get(link_url(token))
            second = self.api().get(link_url(token))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 429, second.content)

    def test_every_use_is_logged(self):
        quote_request, token = self._pair()
        self.api().get(link_url(token))
        self.api().get(link_url(token))
        link = quote_request.client_links.get()
        self.assertEqual(ClientLinkUse.objects.filter(link=link).count(), 2)
        self.assertEqual(link.use_count, 2)
        self.assertIsNotNone(link.last_used_at)

    def test_new_link_replaces_the_old(self):
        quote_request, old_token = self._pair()
        new_token = self.create_link(quote_request, manager=self.manager).data["token"]
        self.assertEqual(self.api().get(link_url(old_token)).status_code, 404)
        self.assertEqual(self.api().get(link_url(new_token)).status_code, 200)

    def test_revoked_link_stops_working(self):
        quote_request = quote_request_from(self.create_request(self.manager))
        created = self.create_link(quote_request)
        token, link_id = created.data["token"], created.data["link_id"]
        revoke = self.api(self.manager).post(
            f"/api/client-portal/manager/links/{link_id}/revoke/", {}, format="json"
        )
        self.assertEqual(revoke.status_code, 200, revoke.content)
        self.assertEqual(self.api().get(link_url(token)).status_code, 404)

    def test_expired_link_stops_working(self):
        quote_request, token = self._pair()
        link = quote_request.client_links.first()
        link.expires_at = timezone.now() - timedelta(minutes=1)
        link.save(update_fields=["expires_at", "updated_at"])
        self.assertEqual(self.api().get(link_url(token)).status_code, 404)

    def test_tampered_token_is_rejected(self):
        _, token = self._pair()
        self.assertEqual(self.api().get(link_url(token + "abc")).status_code, 404)


# ---------------------------------------------------------------------------
# Law 10.5/10.6 — messaging
# ---------------------------------------------------------------------------


class MessagingTests(ClientFlowBase):
    def test_message_is_attributed_to_printy(self):
        body = build_client_message_body("Your print is ready.", manager_name="Jane")
        self.assertIn("This message is from Printy on behalf of Jane.", body)

    def test_delivery_attempt_is_logged(self):
        quote_request = quote_request_from(self.create_request(self.manager))
        self.create_link(quote_request)
        delivery = MessageDelivery.objects.filter(quote_request=quote_request).first()
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.status, MessageDelivery.Status.SENT)
        self.assertIn("on behalf of", delivery.body)

    def test_opt_out_is_honoured(self):
        ClientContactPreference.objects.create(phone_normalized="254700000001", opted_out=True)
        quote_request = quote_request_from(self.create_request(self.manager))
        self.create_link(quote_request)
        delivery = MessageDelivery.objects.filter(quote_request=quote_request).first()
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.status, MessageDelivery.Status.SKIPPED_OPT_OUT)
