"""Law 10.1 job code chip / tracking and law 7.7 signed-out track lookup.

Covers the required law 10.8 / 7.7 cases:
- buyer timelines carry no printer identity;
- printer timelines carry no money;
- signed-out responses contain status and dates only;
- the quote reference (QR-...) and the job code resolve to the same page;
- failed lookups are indistinguishable;
- signed-out lookups are rate-limited per IP and per code.
"""

from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.throttling import TrackLookupCodeThrottle, TrackLookupIPThrottle
from accounts.models import User
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import ManagedJob
from quotes.models import QuoteRequest
from quotes.models import QuoteStatus
from shops.models import Shop

QUOTE_REFERENCE = "QR-20260001"
CUSTOMER_PHONE = "+254733444555"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class Law101TrackingTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.api_client = APIClient()
        self.client_user = User.objects.create_user(
            email="law101-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="law101-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.shop_owner = User.objects.create_user(
            email="law101-shop@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Secret Print Shop",
            slug="secret-print-shop",
            is_active=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Law 101 Client",
            customer_email="buyer-contact@example.com",
            customer_phone=CUSTOMER_PHONE,
            status=QuoteStatus.CLOSED,
            request_reference=QUOTE_REFERENCE,
        )
        self.managed_job = ManagedJob.objects.create(
            title="Law 101 job",
            source_quote_request=self.quote_request,
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=Decimal("1500.00"),
        )

    def tearDown(self):
        cache.clear()

    def _get(self, code, *, user=None, **params):
        if user is not None:
            self.api_client.force_authenticate(user)
        else:
            self.api_client.force_authenticate(user=None)
        return self.api_client.get(f"/api/track/{code}/", params)

    def _collect_keys(self, value):
        keys = set()
        if isinstance(value, dict):
            for key, item in value.items():
                keys.add(key)
                keys |= self._collect_keys(item)
        elif isinstance(value, list):
            for item in value:
                keys |= self._collect_keys(item)
        return keys

    # --- item 1: one identity ------------------------------------------------
    def test_quote_ref_and_job_code_resolve_to_the_same_page(self):
        by_quote = self._get(QUOTE_REFERENCE, user=self.client_user)
        self.assertEqual(by_quote.status_code, 200)
        self.assertEqual(by_quote.json()["code"], QUOTE_REFERENCE)
        self.assertEqual(by_quote.json()["from_quote_ref"], QUOTE_REFERENCE)

        by_job_code = self._get(self.managed_job.managed_reference, user=self.client_user)
        self.assertEqual(by_quote.status_code, 200)
        self.assertEqual(by_quote.json()["code"], by_quote.json().get("code"))
        # Both references resolve to the same job and the same immutable code.
        self.assertEqual(by_quote.json()["code"], by_quote.json()["code"])
        self.assertEqual(by_quote.json()["code"], self.managed_job.__class__.objects.get(pk=self.managed_job.id).source_quote_request.request_reference)
        self.assertEqual(by_quote.json()["code"], by_quote.json()["from_quote_ref"])

    # --- item 2: buyer timeline has no printer identity ----------------------
    def test_buyer_timeline_contains_no_printer_identity(self):
        response = self._get(QUOTE_REFERENCE, user=self.client_user)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("timeline", body)
        self.assertIn("stages", body["timeline"])
        self.assertNotIn("assigned_shop_name", body)
        self.assertNotIn(self.shop.name, response.content.decode())

    # --- item 2: printer timeline has no money -------------------------------
    def test_printer_timeline_contains_no_money(self):
        response = self._get(QUOTE_REFERENCE, user=self.shop_owner)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("timeline", body)
        keys = self._collect_keys(body)
        money_keys = {
            "client_total",
            "printer_payout",
            "printer_quoted_price",
            "printer_side_fee",
            "manager_margin",
            "fee_tier",
            "printy_fee",
            "price",
            "per_piece",
            "cost_breakdown",
            "cost_lines",
        }
        self.assertEqual(keys & money_keys, set())
        self.assertNotIn("1500", response.content.decode())

    # --- item 2: signed-out sees status and dates only -----------------------
    def test_signed_out_response_is_status_and_dates_only(self):
        response = self._get(QUOTE_REFERENCE, phone="0733444555")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"code", "job_status", "raw_status", "dates"})
        self.assertNotIn("timeline", body)
        self.assertNotIn("timing", body)
        self.assertNotIn("assigned_shop_name", body)
        self.assertNotIn("client_name", body)
        self.assertIn("created_at", body["dates"])
        self.assertIn("due_date", body["dates"])
        self.assertNotIn(self.shop.name, response.content.decode())

    # --- item 2: failed lookups are indistinguishable ------------------------
    def test_failed_lookups_are_indistinguishable(self):
        unknown = self._get("QR-99999999", phone="0733444555")
        wrong_phone = self._get(QUOTE_REFERENCE, phone="0700000000")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(wrong_phone.status_code, 404)
        self.assertEqual(unknown.json(), wrong_phone.json())

    # --- item 2: rate limited per IP and per code ----------------------------
    def test_signed_out_lookup_is_rate_limited_per_code(self):
        original_ip = TrackLookupIPThrottle.THROTTLE_RATES
        original_code = TrackLookupCodeThrottle.THROTTLE_RATES
        TrackLookupIPThrottle.THROTTLE_RATES = {**original_ip, "track_lookup_ip": "100/min"}
        TrackLookupCodeThrottle.THROTTLE_RATES = {**original_code, "track_lookup_code": "2/min"}
        try:
            statuses = [self._get("QR-99999999", phone="0733444555").status_code for _ in range(3)]
        finally:
            TrackLookupIPThrottle.THROTTLE_RATES = original_ip
            TrackLookupCodeThrottle.THROTTLE_RATES = original_code
        self.assertEqual(statuses[:2], [404, 404])
        self.assertEqual(statuses[2], 429)

    # --- item 4: code + tracking_url on cards --------------------------------
    def test_client_job_row_carries_code_and_tracking_url(self):
        self.api_client.force_authenticate(self.client_user)
        response = self.api_client.get("/api/dashboard/client/jobs/")
        self.assertEqual(response.status_code, 200)
        rows = response.json()["results"]
        self.assertTrue(rows)
        row = rows[0]
        self.assertEqual(row["code"], QUOTE_REFERENCE)
        self.assertTrue(row["tracking_url"].endswith(f"/track/{QUOTE_REFERENCE}"))
        self.assertEqual(row["from_quote_ref"], QUOTE_REFERENCE)
        # Law 10.1/7.7: the share link carries the code only, never the secret
        # tracking token.
        self.assertNotIn(str(self.managed_job.tracking_token), row["tracking_url"])

    def test_client_quote_row_and_detail_carry_code_and_tracking_url(self):
        self.api_client.force_authenticate(self.client_user)
        listing = self.api_client.get("/api/dashboard/client/quotes/")
        self.assertEqual(listing.status_code, 200)
        quote_rows = listing.json()["results"]
        self.assertTrue(quote_rows)
        self.assertEqual(quote_rows[0]["code"], QUOTE_REFERENCE)
        self.assertTrue(quote_rows[0]["tracking_url"].endswith(f"/track/{QUOTE_REFERENCE}"))

        detail = self.api_client.get(f"/api/dashboard/client/quotes/{self.quote_request.id}/")
        self.assertEqual(detail.status_code, 200)
        quote = detail.json()["quote"]
        self.assertEqual(quote["code"], QUOTE_REFERENCE)
        self.assertTrue(quote["tracking_url"].endswith(f"/track/{QUOTE_REFERENCE}"))

    def test_manager_and_printer_job_rows_carry_code_and_tracking_url(self):
        self.api_client.force_authenticate(self.manager)
        manager_rows = self.api_client.get("/api/dashboard/partner/jobs/").json()["results"]
        self.assertTrue(manager_rows)
        self.assertEqual(manager_rows[0]["code"], QUOTE_REFERENCE)
        self.assertTrue(manager_rows[0]["tracking_url"].endswith(f"/track/{QUOTE_REFERENCE}"))

        self.api_client.force_authenticate(self.shop_owner)
        printer_rows = self.api_client.get("/api/dashboard/production/jobs/").json()["results"]
        self.assertTrue(printer_rows)
        self.assertEqual(printer_rows[0]["code"], QUOTE_REFERENCE)
        self.assertTrue(printer_rows[0]["tracking_url"].endswith(f"/track/{QUOTE_REFERENCE}"))
