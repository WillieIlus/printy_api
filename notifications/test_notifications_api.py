"""Phase 4 — notification guarantees (backend delivery layer).

The DB-backed Notification model, trigger sites and the REST endpoints already
exist; these tests pin the guarantees 4.1 requires:

1. Notifications are created when users contact each other (the Phase 0 L
   complaint, via the ask-question / reply conversation flow).
2. ``notify`` creates rows with the expected fields.
3. ``GET /api/me/notifications/`` is paginated, newest-first and never leaks
   another user's notifications.
4. unread-count / mark-read / mark-all-read work and are per-user.
5. A user can never mark or see another user's notification (403 on
   mark-read of a foreign row).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from notifications.models import Notification
from notifications.services import notify
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from shops.models import Shop

User = get_user_model()


class NotificationApiTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(email="notif-buyer@test.com", password="pw")
        self.seller = User.objects.create_user(email="notif-seller@test.com", password="pw")
        self.outsider = User.objects.create_user(email="notif-outsider@test.com", password="pw")
        self.shop = Shop.objects.create(owner=self.seller, name="Notif Shop", slug="notif-shop", is_active=True)

    def _submitted_request(self):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            customer_name="Notif Buyer",
            customer_email="notif-buyer@test.com",
            status=QuoteStatus.SUBMITTED,
        )
        return quote_request

    # ── notify() row creation ──────────────────────────────────────────────

    def test_notify_creates_row_with_expected_fields(self):
        notification = notify(
            recipient=self.buyer,
            notification_type=Notification.SHOP_QUESTION_ASKED,
            message="Please confirm the finish.",
            object_type="quote",
            object_id=42,
            actor=self.seller,
        )
        notification.refresh_from_db()
        self.assertEqual(notification.user, self.buyer)
        self.assertEqual(notification.actor, self.seller)
        self.assertEqual(notification.notification_type, Notification.SHOP_QUESTION_ASKED)
        self.assertEqual(notification.object_id, 42)
        self.assertIsNone(notification.read_at)
        self.assertFalse(notification.is_read)

    # ── contact-between-users fires notifications (Phase 0 complaint) ──────

    def test_conversation_contact_fires_both_sides(self):
        quote_request = self._submitted_request()

        self.client.force_authenticate(user=self.seller)
        asked = self.client.post(
            f"/api/shops/{self.shop.slug}/incoming-requests/{quote_request.id}/ask-question/",
            {"body": "Confirm the final size, please."},
            format="json",
        )
        self.assertEqual(asked.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                user=self.buyer,
                notification_type=Notification.SHOP_QUESTION_ASKED,
                object_id=quote_request.id,
                actor=self.seller,
            ).exists()
        )

        self.client.force_authenticate(user=self.buyer)
        replied = self.client.post(
            f"/api/quote-requests/{quote_request.id}/reply/",
            {"body": "90 x 55 mm it is."},
            format="json",
        )
        self.assertEqual(replied.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                user=self.seller,
                notification_type=Notification.BUYER_CLARIFICATION_SENT,
                object_id=quote_request.id,
                actor=self.buyer,
            ).exists()
        )

    def test_submit_fires_shop_owner_and_submitter_notifications(self):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            customer_name="Notif Buyer",
            customer_email="notif-buyer@test.com",
            status=QuoteStatus.DRAFT,
        )

        self.client.force_authenticate(user=self.buyer)
        submitted = self.client.post(f"/api/quote-requests/{quote_request.id}/submit/")
        self.assertEqual(submitted.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                user=self.seller,
                notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
                object_id=quote_request.id,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.buyer,
                notification_type=Notification.QUOTE_REQUEST_SENT,
                object_id=quote_request.id,
            ).exists()
        )

    # ── polling endpoints: pagination, isolation, mark-read ────────────────

    def test_list_is_paginated_newest_first_and_own_only(self):
        for i in range(25):
            Notification.objects.create(
                user=self.buyer,
                actor=self.seller,
                notification_type=Notification.SHOP_QUESTION_ASKED,
                object_type="quote",
                object_id=i,
                message=f"n{i}",
            )
        Notification.objects.create(
            user=self.seller,
            actor=self.buyer,
            notification_type=Notification.BUYER_CLARIFICATION_SENT,
            object_type="quote",
            object_id=1,
            message="seller-only",
        )

        self.client.force_authenticate(user=self.buyer)
        response = self.client.get("/api/me/notifications/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 25)
        self.assertEqual(len(payload["results"]), 20)
        self.assertIsNotNone(payload["next"])
        messages = {row["message"] for row in payload["results"]}
        self.assertNotIn("seller-only", messages)
        ids = [row["id"] for row in payload["results"]]
        created = [row["created_at"] for row in payload["results"]]
        self.assertEqual(created, sorted(created, reverse=True))

        self.client.force_authenticate(user=self.seller)
        seller_payload = self.client.get("/api/me/notifications/").json()
        self.assertEqual(seller_payload["count"], 1)
        self.assertEqual(seller_payload["results"][0]["message"], "seller-only")

    def test_unread_count_is_per_user(self):
        for i in range(2):
            Notification.objects.create(user=self.buyer, notification_type=Notification.QUOTE_REQUEST_SENT, object_id=i)
        Notification.objects.create(user=self.seller, notification_type=Notification.QUOTE_REQUEST_SUBMITTED, object_id=1)

        self.client.force_authenticate(user=self.buyer)
        self.assertEqual(self.client.get("/api/me/notifications/unread-count/").json()["count"], 2)
        self.client.force_authenticate(user=self.seller)
        self.assertEqual(self.client.get("/api/me/notifications/unread-count/").json()["count"], 1)
        self.client.force_authenticate(user=self.outsider)
        self.assertEqual(self.client.get("/api/me/notifications/unread-count/").json()["count"], 0)

    def test_mark_read_then_mark_all_read(self):
        first = Notification.objects.create(user=self.buyer, notification_type=Notification.QUOTE_REQUEST_SENT, object_id=1)
        second = Notification.objects.create(user=self.buyer, notification_type=Notification.QUOTE_REQUEST_SENT, object_id=2)

        self.client.force_authenticate(user=self.buyer)
        marked = self.client.patch(f"/api/me/notifications/{first.id}/mark-read/")
        self.assertEqual(marked.status_code, 200)
        first.refresh_from_db()
        self.assertTrue(first.is_read)
        self.assertEqual(self.client.get("/api/me/notifications/unread-count/").json()["count"], 1)

        all_read = self.client.patch("/api/me/notifications/mark-all-read/")
        self.assertEqual(all_read.status_code, 200)
        self.assertEqual(all_read.json()["marked"], 1)
        second.refresh_from_db()
        self.assertTrue(second.is_read)
        self.assertEqual(self.client.get("/api/me/notifications/unread-count/").json()["count"], 0)

    def test_user_cannot_mark_another_users_notification(self):
        foreign = Notification.objects.create(
            user=self.buyer,
            actor=self.seller,
            notification_type=Notification.SHOP_QUESTION_ASKED,
            object_type="quote",
            object_id=1,
        )
        self.client.force_authenticate(user=self.outsider)
        response = self.client.patch(f"/api/me/notifications/{foreign.id}/mark-read/")
        self.assertEqual(response.status_code, 404)

    def test_user_never_sees_another_users_notifications_queryset_roots(self):
        for i in range(3):
            Notification.objects.create(user=self.buyer, notification_type=Notification.QUOTE_REQUEST_SENT, object_id=i)
        Notification.objects.create(user=self.seller, notification_type=Notification.QUOTE_REQUEST_SUBMITTED, object_id=1)

        self.client.force_authenticate(user=self.outsider)
        payload = self.client.get("/api/me/notifications/").json()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["results"], [])