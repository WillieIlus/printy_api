"""Law 10.6 — the manager can read a client link's status.

Covers prompt item 2 ("manager can see link status: sent, viewed, expired") and
the resend/revoke affordances it drives. Status is derived from stored backend
fields only, never guessed client-side.
"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from quotes.models import QuoteRequest

MANAGER_CREATE_URL = "/api/client-portal/manager/quote-requests/"


def status_url(quote_request_id: int) -> str:
    return f"{MANAGER_CREATE_URL}{quote_request_id}/link/"


class LinkStatusTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.manager = User.objects.create_user(
            email="m1@example.com", password="pw12345678", name="Manager One", role=User.Role.PARTNER
        )
        self.manager2 = User.objects.create_user(
            email="m2@example.com", password="pw12345678", name="Manager Two", role=User.Role.PARTNER
        )

    def api(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def make_request(self, manager=None, phone="0700000001", name="Client One") -> QuoteRequest:
        resp = self.api(manager or self.manager).post(
            MANAGER_CREATE_URL,
            {"name": name, "phone": phone, "contact_permitted": True},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        return QuoteRequest.objects.get(pk=resp.data["quote_request_id"])

    def create_link(self, quote_request, manager=None):
        return self.api(manager or self.manager).post(status_url(quote_request.id), {}, format="json")

    def test_status_is_none_without_a_link(self):
        quote_request = self.make_request()
        resp = self.api(self.manager).get(status_url(quote_request.id))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.data["has_link"])
        self.assertEqual(resp.data["status"], "none")

    def test_status_is_sent_before_any_open(self):
        quote_request = self.make_request()
        self.create_link(quote_request)
        resp = self.api(self.manager).get(status_url(quote_request.id))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["has_link"])
        self.assertEqual(resp.data["status"], "sent")
        self.assertEqual(resp.data["view_count"], 0)
        self.assertIsNone(resp.data["viewed_at"])

    def test_status_becomes_viewed_after_an_open(self):
        quote_request = self.make_request()
        token = self.create_link(quote_request).data["token"]
        self.api().get(f"/api/client-portal/links/{token}/")

        resp = self.api(self.manager).get(status_url(quote_request.id))
        self.assertEqual(resp.data["status"], "viewed")
        self.assertEqual(resp.data["view_count"], 1)
        self.assertIsNotNone(resp.data["viewed_at"])

    def test_status_is_expired_when_past_expiry(self):
        quote_request = self.make_request()
        self.create_link(quote_request)
        link = quote_request.client_links.first()
        link.expires_at = timezone.now() - timedelta(minutes=1)
        link.save(update_fields=["expires_at", "updated_at"])
        resp = self.api(self.manager).get(status_url(quote_request.id))
        self.assertEqual(resp.data["status"], "expired")
        self.assertFalse(resp.data["is_active"])

    def test_status_is_revoked_after_revoke(self):
        quote_request = self.make_request()
        created = self.create_link(quote_request)
        self.api(self.manager).post(
            f"/api/client-portal/manager/links/{created.data['link_id']}/revoke/", {}, format="json"
        )
        resp = self.api(self.manager).get(status_url(quote_request.id))
        self.assertEqual(resp.data["status"], "revoked")
        self.assertFalse(resp.data["is_active"])

    def test_resend_replaces_and_reports_the_new_link(self):
        quote_request = self.make_request()
        first = self.create_link(quote_request)
        second = self.create_link(quote_request)
        self.assertNotEqual(first.data["link_id"], second.data["link_id"])
        resp = self.api(self.manager).get(status_url(quote_request.id))
        self.assertEqual(resp.data["link_id"], second.data["link_id"])
        self.assertEqual(resp.data["status"], "sent")

    def test_another_manager_cannot_read_the_status(self):
        quote_request = self.make_request()
        self.create_link(quote_request)
        resp = self.api(self.manager2).get(status_url(quote_request.id))
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_unauthenticated_manager_is_refused(self):
        quote_request = self.make_request()
        self.create_link(quote_request)
        resp = self.api(None).get(status_url(quote_request.id))
        self.assertIn(resp.status_code, (401, 403))
