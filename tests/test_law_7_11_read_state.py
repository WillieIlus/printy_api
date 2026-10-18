"""Law 7.11: per-viewer read state for quotes and jobs.

Opening an item sets a per-viewer ``read_at`` and returns an ``unread`` flag in
the list and detail payloads. Read state is per viewer (one viewer reading an
item must not clear another's unread flag) and it must never change the item's
``section``, ``needs_attention`` or ``muted`` flags.
"""

from rest_framework.test import APIClient, APITestCase

from accounts.models import User
from jobs.models import ManagedJob
from jobs.read_state import JOB, QUOTE_REQUEST, ViewerItemReadState, mark_read, read_map
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest


class Law711ReadStateTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.other_viewer = User.objects.create_user(
            email="law711-other@test.com", password="pass12345", role=User.Role.CLIENT
        )
        self.buyer = User.objects.create_user(
            email="law711-buyer@test.com", password="pass12345", role=User.Role.CLIENT
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=None,
            created_by=self.buyer,
            customer_name="Read State Buyer",
            customer_email=self.buyer.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "source": "manager_led_intake",
                "calculator_inputs": {"product_type": "business_card", "quantity": 100},
            },
        )
        self.job = ManagedJob.objects.create(
            title="Read State Job",
            client=self.buyer,
            created_by=self.buyer,
            status="in_production",
            payment_status="confirmed",
        )

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def _client_quote_list(self):
        return self.client.get("/api/dashboard/client/quotes/")

    def _client_job_list(self):
        return self.client.get("/api/dashboard/client/jobs/")

    def test_quote_starts_unread_then_read_on_open(self):
        self._auth(self.buyer)
        rows = self._client_quote_list().json()["results"]
        row = next(item for item in rows if item["id"] == self.quote_request.id)
        self.assertTrue(row["unread"])
        self.assertIsNone(row["viewer_read_at"])
        flags_before = {k: row.get(k) for k in ("section", "needs_attention", "muted")}

        detail = self.client.get(f"/api/dashboard/client/quotes/{self.quote_request.id}/")
        self.assertEqual(detail.status_code, 200, detail.json())
        self.assertFalse(detail.json()["unread"])

        rows_after = self._client_quote_list().json()["results"]
        row_after = next(item for item in rows_after if item["id"] == self.quote_request.id)
        self.assertFalse(row_after["unread"])
        self.assertIsNotNone(row_after["viewer_read_at"])
        flags_after = {k: row_after.get(k) for k in ("section", "needs_attention", "muted")}
        # Law 7.11: read state never changes the section/attention/muted flags.
        self.assertEqual(flags_before, flags_after)

    def test_job_starts_unread_then_read_on_open(self):
        self._auth(self.buyer)
        rows = self._client_job_list().json()["results"]
        row = next(item for item in rows if item["id"] == self.job.id)
        self.assertTrue(row["unread"])
        flags_before = {k: row.get(k) for k in ("section", "needs_attention", "muted")}

        detail = self.client.get(f"/api/dashboard/client/jobs/{self.job.id}/")
        self.assertEqual(detail.status_code, 200, detail.json())
        self.assertFalse(detail.json()["unread"])
        self.assertFalse(detail.json()["job"]["unread"])

        rows_after = self._client_job_list().json()["results"]
        row_after = next(item for item in rows_after if item["id"] == self.job.id)
        self.assertFalse(row_after["unread"])
        flags_after = {k: row_after.get(k) for k in ("section", "needs_attention", "muted")}
        self.assertEqual(flags_before, flags_after)

    def test_read_state_is_per_viewer(self):
        # The other viewer reading their own item must not affect the buyer.
        mark_read(viewer=self.other_viewer, item_type=QUOTE_REQUEST, item_id=self.quote_request.id)
        read_states = read_map(
            viewer=self.buyer, item_type=QUOTE_REQUEST, item_ids=[self.quote_request.id]
        )
        self.assertNotIn(self.quote_request.id, read_states)

        self._auth(self.buyer)
        rows = self._client_quote_list().json()["results"]
        row = next(item for item in rows if item["id"] == self.quote_request.id)
        self.assertTrue(row["unread"])

    def test_mark_read_is_idempotent(self):
        first = mark_read(viewer=self.buyer, item_type=JOB, item_id=self.job.id)
        second = mark_read(viewer=self.buyer, item_type=JOB, item_id=self.job.id)
        self.assertEqual(first, second)
        self.assertEqual(
            ViewerItemReadState.objects.filter(
                viewer=self.buyer, item_type=JOB, item_id=self.job.id
            ).count(),
            1,
        )
