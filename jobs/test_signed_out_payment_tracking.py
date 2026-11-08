"""Account-free payment and signed-out job tracking (law 10-11).

A client with no Printy account pays a sent quote through the single-job link;
afterwards they track the job with the job code plus the phone on the job. The
tracking payload carries the stages, their timestamps and who has the ball, and
never shop identity, production cost or the manager's margin.
"""
from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from jobs.job_codes import job_code
from jobs.managed_services import create_managed_job_from_accepted_quote
from payments.models import Payment
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.acceptance import accept_quote_for_payment
from quotes.models import Quote, QuoteRequest
from shops.models import Shop

User = get_user_model()

PHONE = "254733444555"
LOCAL_PHONE = "0733444555"
SHOP_NAME = "Secret Print Shop"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SignedOutPaymentAndTrackingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.manager = User.objects.create(
            email="anon-manager@example.test", role=User.Role.BROKER, name="Mia Manager"
        )
        self.shop = Shop.objects.create(
            owner=self.manager, name=SHOP_NAME, slug="secret-print-shop", is_active=True
        )
        self.request = QuoteRequest.objects.create(
            created_by=self.manager,
            assigned_manager=self.manager,
            created_by_manager=self.manager,
            customer_name="Cee Client",
            customer_phone=PHONE,
            status=QuoteRequest.QUOTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            created_by=self.manager,
            total=Decimal("1600.00"),
            status=Quote.SENT,
        )
        create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1600.00"),
        )

    def tearDown(self):
        cache.clear()

    def _pay_signed_out(self):
        accepted_quote, payment = accept_quote_for_payment(
            quote=self.quote,
            accepted_by=None,
            payer_phone=PHONE,
            authorized=True,
        )
        return accepted_quote, payment

    def _track(self, code, *, phone=None):
        params = {"phone": phone} if phone is not None else {}
        return self.api.get(f"/api/track/{code}/", params)

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

    # 11. unpaid/signed-out client can pay --------------------------------
    def test_a_quote_with_no_account_can_be_paid_without_signing_in(self):
        accepted_quote, payment = self._pay_signed_out()
        accepted_quote.refresh_from_db()
        self.assertEqual(accepted_quote.status, Quote.ACCEPTED)
        self.assertIsNone(getattr(accepted_quote, "accepted_by_id", None))
        payment.refresh_from_db()
        self.assertIsNone(payment.payer_id)
        self.assertTrue(str(payment.payer_phone or "").endswith("444555"), payment.payer_phone)
        self.assertEqual(Payment.objects.filter(payer__isnull=True).count(), 1)

    # 11. tracking works with the job code and the phone on the job --------
    def test_tracking_by_job_code_and_phone_works_and_a_wrong_phone_fails(self):
        accepted_quote, _payment = self._pay_signed_out()
        job = create_managed_job_from_accepted_quote(
            quote_request=self.request, quote=accepted_quote, accepted_by=None
        )
        self.assertIsNone(job.client_id)
        code = job_code(job)

        response = self._track(code, phone=LOCAL_PHONE)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["code"], code)
        self.assertTrue(body["stages"])
        self.assertTrue(body["who_has_the_ball"])
        self.assertTrue(
            any(stage.get("started_at") or stage.get("completed_at") for stage in body["stages"])
        )

        # The full international number matches too.
        self.assertEqual(self._track(code, phone=PHONE).status_code, 200)

        wrong_phone = self._track(code, phone="0700000000")
        no_phone = self._track(code)
        self.assertEqual(wrong_phone.status_code, 404)
        self.assertEqual(no_phone.status_code, 404)
        unknown_code = self._track("QR-99999999", phone=LOCAL_PHONE)
        self.assertEqual(unknown_code.status_code, 404)
        # A wrong phone and an unknown code are indistinguishable.
        self.assertEqual(wrong_phone.json(), unknown_code.json())

    # 12. no shop identity, costs or margin in the tracking payload --------
    def test_tracking_payload_has_no_shop_identity_costs_or_margin(self):
        accepted_quote, _payment = self._pay_signed_out()
        job = create_managed_job_from_accepted_quote(
            quote_request=self.request, quote=accepted_quote, accepted_by=None
        )
        job.assigned_shop = self.shop
        job.save(update_fields=["assigned_shop", "updated_at"])

        response = self._track(job_code(job), phone=LOCAL_PHONE)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()

        content = response.content.decode()
        self.assertNotIn(SHOP_NAME, content)
        self.assertNotIn("Northpress", content)
        keys = self._collect_keys(body)
        for forbidden in (
            "shop",
            "shop_name",
            "assigned_shop",
            "assigned_shop_name",
            "printer",
            "printer_name",
            "production_cost",
            "printy_fee",
            "manager_margin",
            "margin",
            "markup",
            "payout",
            "client_total",
        ):
            self.assertNotIn(forbidden, keys)
        blob = json.dumps(body).lower()
        for forbidden in ("printy_fee", "production_cost", "manager_margin", "shop"):
            self.assertNotIn(forbidden, blob)
        self.assertNotIn("1600", content)
        self.assertNotIn("1000", content)
