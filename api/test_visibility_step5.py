from types import SimpleNamespace

from django.test import SimpleTestCase

from api.visibility import project_public_marketplace_response, strip_forbidden_keys
from jobs.managed_job_actor_serializers import ManagedJobClientSerializer, ManagedJobShopSerializer
from payments.payment_actor_serializers import PaymentClientSerializer
from payments.payment_actor_serializers import PaymentAdminSerializer
from quotes.quote_actor_serializers import QuoteAdminSerializer, QuoteBrokerSerializer, QuoteClientSerializer
from quotes.quote_request_actor_serializers import QuoteRequestClientSerializer


class _Collection:
    def __init__(self, value=None):
        self.value = value

    def order_by(self, *args):
        return self

    def filter(self, *args, **kwargs):
        return self

    def exclude(self, *args, **kwargs):
        return self

    def first(self):
        return self.value

    def all(self):
        return []

    def __getitem__(self, key):
        return []


class _Field:
    def __init__(self, name):
        self.name = name


class Step5VisibilitySerializerTests(SimpleTestCase):
    CLIENT_FORBIDDEN = {
        "production_cost",
        "shop_payout",
        "broker_payout",
        "printy_fee",
        "broker_margin",
        "broker_margin_fee",
        "printer_side_fee",
        "gross_margin",
        "shop_id",
        "shop_name",
        "shop_slug",
        "request_snapshot",
        "response_snapshot",
        "revised_pricing_snapshot",
        "pricing_snapshot",
        "platform_fee",
        "raw_callback",
        "raw_response",
        "competing_shop_rates",
        "internal_formula",
        "pricing_formula",
        "revised_pricing_snapshot",
    }
    SHOP_FORBIDDEN = {
        "client_total",
        "broker_margin",
        "broker_margin_fee",
        "broker_payout",
        "printy_fee",
        "other_shop_name",
        "request_snapshot",
        "response_snapshot",
        "pricing_snapshot",
        "platform_fee",
        "raw_callback",
        "raw_response",
        "competing_shop_rates",
    }

    def _assert_absent(self, payload, forbidden, path="root"):
        if isinstance(payload, dict):
            for key, value in payload.items():
                self.assertNotIn(key, forbidden, f"{key} leaked at {path}")
                self._assert_absent(value, forbidden, f"{path}.{key}")
        elif isinstance(payload, list):
            for index, value in enumerate(payload):
                self._assert_absent(value, forbidden, f"{path}[{index}]")

    def _split(self):
        return SimpleNamespace(
            id=7,
            production_cost="1000.00",
            broker_client_price="1500.00",
            gross_margin="500.00",
            printer_side_fee="100.00",
            broker_margin_fee="100.00",
            printy_fee="200.00",
            shop_payout="1000.00",
            broker_payout="300.00",
            client_total="1500.00",
            max_allowed_client_price="4000.00",
            applied_markup_multiple="1.5000",
            policy_used_id=3,
            production_option_id=4,
            quote_id=5,
            calculated_at="now",
        )

    def _quote(self):
        return SimpleNamespace(
            id=5,
            quote_reference="QS-5",
            status="sent",
            client_total="1500.00",
            total="1500.00",
            estimated_ready_at="2026-06-10T10:00:00Z",
            expires_at="2026-06-11T10:00:00Z",
            accepted_at=None,
            created_at="2026-06-04T10:00:00Z",
            updated_at="2026-06-04T10:00:00Z",
            sent_at="2026-06-04T10:05:00Z",
            quote_request_id=9,
            production_option_id=4,
            financial_split=self._split(),
            note="Business cards",
            response_snapshot={
                "shop_id": 1,
                "shop_name": "Private Shop",
                "production_cost": "1000.00",
                "printy_fee": "200.00",
                "internal_pricing_snapshot": {"broker_payout": "300.00"},
            },
            revised_pricing_snapshot={"pricing_snapshot": {"printy_fee": "200.00"}},
            shop_id=1,
            shop=SimpleNamespace(name="Private Shop"),
            _meta=SimpleNamespace(fields=[_Field("id"), _Field("response_snapshot"), _Field("total")]),
        )

    def _quote_request(self):
        item = SimpleNamespace(
            title="Business cards",
            quantity=100,
            spec_text="350gsm cards",
            paper_id=2,
            chosen_width_mm=90,
            chosen_height_mm=50,
            product_id=8,
        )
        return SimpleNamespace(
            id=9,
            status="quoted",
            created_at="2026-06-04T10:00:00Z",
            updated_at="2026-06-04T10:00:00Z",
            request_snapshot={
                "shop_id": 1,
                "shop_name": "Private Shop",
                "shop_slug": "private-shop",
                "production_cost": "1000.00",
                "shop_payout": "1000.00",
                "broker_margin": "500.00",
                "broker_payout": "300.00",
                "printy_fee": "200.00",
                "platform_fee": "200.00",
                "internal_formula": "secret",
                "pricing_formula": "secret",
                "pricing_snapshot": {"competing_shop_rates": []},
                "calculator_inputs": {"quantity": 100},
            },
            items=_Collection(item),
        )

    def test_client_quote_response_has_no_private_financial_or_snapshot_fields(self):
        payload = QuoteClientSerializer(self._quote()).data
        self._assert_absent(payload, self.CLIENT_FORBIDDEN)
        self.assertEqual(payload["client_total"], "1500.00")

    def test_client_quote_request_response_has_no_private_financial_or_snapshot_fields(self):
        payload = QuoteRequestClientSerializer(self._quote_request()).data
        self._assert_absent(payload, self.CLIENT_FORBIDDEN)
        self.assertIn("public_draft_snapshot", payload)

    def test_shop_job_response_has_only_shop_payout_from_financials(self):
        split = self._split()
        assignment = SimpleNamespace(shop_payout="1000.00", due_at="2026-06-10", assignment_notes="Print cleanly")
        job = SimpleNamespace(
            id=11,
            status="assigned",
            requested_deadline="2026-06-10",
            operational_snapshot={
                "production_cost": "1000.00",
                "client_total": "1500.00",
                "printy_fee": "200.00",
                "other_shop_name": "Other Shop",
                "quantity": 100,
            },
            source_quote=SimpleNamespace(financial_split=split),
            assignments=_Collection(assignment),
            events=_Collection(),
        )
        payload = ManagedJobShopSerializer(job).data
        self.assertEqual(payload["shop_payout"], "1000.00")
        self._assert_absent(payload, self.SHOP_FORBIDDEN)

    def test_broker_quote_response_contains_canonical_split_fields(self):
        payload = QuoteBrokerSerializer(self._quote()).data
        split = payload["financial_split"]
        for key in ("production_cost", "gross_margin", "printy_fee", "broker_payout", "client_total"):
            self.assertIn(key, split)
        self.assertNotIn("response_snapshot", payload)
        self.assertNotIn("internal_pricing_snapshot", payload)

    def test_public_preview_response_has_no_shop_identity_or_internal_financials(self):
        payload = project_public_marketplace_response(
            {
                "matches": [
                    {
                        "shop_id": 1,
                        "shop_name": "Private Shop",
                        "shop_slug": "private-shop",
                        "production_cost": "1000.00",
                        "printy_fee": "200.00",
                        "price_range": {"min": "1400.00", "max": "1600.00"},
                    }
                ],
                "estimate_min": "1400.00",
                "estimate_max": "1600.00",
            }
        )
        self._assert_absent(payload, self.CLIENT_FORBIDDEN)

    def test_payment_client_response_has_no_split_details(self):
        payment = SimpleNamespace(
            id=3,
            status="paid",
            STATUS_PAID="paid",
            amount="1500.00",
            currency="KES",
            provider="mpesa",
            account_reference="QUOTE-QS-5",
            mpesa_receipt_number="ABC123",
            confirmed_at="2026-06-04T11:00:00Z",
        )
        payload = PaymentClientSerializer(payment).data
        self._assert_absent(payload, self.CLIENT_FORBIDDEN | self.SHOP_FORBIDDEN)

    def test_admin_payment_response_contains_all_model_fields(self):
        payment = SimpleNamespace(
            id=3,
            amount="1500.00",
            status="paid",
            raw_response={"provider": "ok"},
            raw_callback={"callback": "ok"},
            _meta=SimpleNamespace(fields=[
                _Field("id"),
                _Field("amount"),
                _Field("status"),
                _Field("raw_response"),
                _Field("raw_callback"),
            ]),
        )
        payload = PaymentAdminSerializer(payment).data
        self.assertIn("raw_response", payload)
        self.assertIn("raw_callback", payload)

    def test_admin_quote_response_contains_raw_fields(self):
        payload = QuoteAdminSerializer(self._quote()).data
        self.assertIn("response_snapshot", payload)
        self.assertIn("production_cost", str(payload["response_snapshot"]))

    def test_forbidden_key_filter_removes_recursive_private_fields(self):
        payload = strip_forbidden_keys(
            {
                "shop_id": 1,
                "nested": {
                    "request_snapshot": {"shop_name": "Private Shop"},
                    "safe": "ok",
                },
                "client_total": "1500.00",
            },
            "client",
        )
        self._assert_absent(payload, self.CLIENT_FORBIDDEN)
        self.assertEqual(payload["nested"]["safe"], "ok")
