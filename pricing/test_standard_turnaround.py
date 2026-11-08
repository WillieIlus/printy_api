"""LAW 17-22 — standard turnaround, paid speed options and the job total.

Every test here asserts a law line directly:
 17 shop completeness requires a standard turnaround
 18 Faster = +50%, Fastest = +100%, from config constants
 19 rush hours derive from standard, overridable and switchable
 20 ready-by is business hours and always an estimate
 21 the chosen ready-by becomes the job due date
 22 the surcharge runs through the same fee logic (waiver included)
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from jobs.models import ManagedJob
from jobs.timing_services import set_due_date_at_acceptance
from pricing.models import (
    PrintingRate,
    QuantityPricingTier,
    ShopSpeedTierOptIn,
    SpeedTier,
)
from pricing.services.platform_fee_policy import calculate_financial_split
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteRequest
from quotes.partner_services import build_partner_quote_preview
from services.pricing.speed_config import (
    FASTER_SURCHARGE_PERCENT,
    FASTEST_SURCHARGE_PERCENT,
    derived_turnaround_hours,
)
from services.pricing.speed_tiers import (
    apply_speed_selection,
    build_speed_options,
    buyer_speed_payload,
    select_speed_option,
)
from shops.models import Shop
from shops.services import rate_card_missing_steps, shop_rate_card_is_usable

EAT = ZoneInfo("Africa/Nairobi")


def monday(hour: int = 9, *, day: date | None = None) -> datetime:
    day = day or date(2026, 11, 2)  # Monday
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=EAT)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StandardTurnaroundLawTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="law-printer@printy.test", password="pass12345", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Law Press",
            slug="law-press",
            is_active=True,
            opening_time="08:00",
            closing_time="18:00",
        )
        QuantityPricingTier.objects.create(
            name="Law test tier",
            min_sheets=1,
            max_sheets=None,
            multiplier=Decimal("1.50"),
            minimum_order_floor=Decimal("700.00"),
        )

    def _priced_shop(self, *, standard_hours=None) -> Shop:
        machine = Machine.objects.create(
            shop=self.shop, name="Law Press Machine", max_width_mm=320, max_height_mm=450, is_active=True
        )
        Paper.objects.create(
            shop=self.shop,
            name="300gsm Gloss",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
        )
        if standard_hours is not None:
            self.shop.standard_turnaround_hours = standard_hours
            self.shop.save(update_fields=["standard_turnaround_hours"])
        return self.shop

    def _opt_in(self, *keys, hours: int | None = None, active: bool = True):
        for key in keys:
            tier = SpeedTier.objects.get(key=key)
            ShopSpeedTierOptIn.objects.update_or_create(
                shop=self.shop,
                tier=tier,
                defaults={"is_active": active, "target_hours": hours},
            )

    def _options(self, *, base: str = "1000.00", start: datetime | None = None, category: str = "flyer"):
        return build_speed_options(
            shop=self.shop,
            product_category=category,
            base_production_cost=Decimal(base),
            start_at=start or monday(9),
        )

    # LAW 17 -----------------------------------------------------------------
    def test_shop_without_a_standard_turnaround_is_incomplete(self):
        self._priced_shop()

        self.assertFalse(shop_rate_card_is_usable(self.shop))
        codes = [step["code"] for step in rate_card_missing_steps(self.shop)]
        self.assertIn("standard_turnaround", codes)

        self.shop.standard_turnaround_hours = 48
        self.shop.save(update_fields=["standard_turnaround_hours"])

        self.assertTrue(shop_rate_card_is_usable(self.shop))
        codes = [step["code"] for step in rate_card_missing_steps(self.shop)]
        self.assertNotIn("standard_turnaround", codes)

    # LAW 18 + 22 ------------------------------------------------------------
    def test_faster_and_fastest_totals_agree_with_preview_sent_quote_and_split(self):
        self._priced_shop(standard_hours=48)
        self._opt_in("faster", "fastest")
        productions_inputs = {
            "quantity": 100,
            "yield_per_sheet": 10,
            "paper_cost_per_sheet": "10.00",
            "click_charge_per_sheet": "5.00",
            "finishing_cost": "0.00",
        }

        # 1) preview: the option list carries base, +50% and +100%.
        speeds = {
            item["tier"]["key"]: item for item in self._options(base="700.00")
        }
        self.assertEqual(speeds["standard"]["production_total"], "700.00")
        self.assertEqual(speeds["faster"]["production_total"], "1050.00")
        self.assertEqual(speeds["fastest"]["production_total"], "1400.00")

        preview = build_partner_quote_preview(
            pricing_snapshot={"production_cost_inputs": productions_inputs},
            shop=self.shop,
            partner_markup=Decimal("300.00"),
        )
        self.assertEqual(preview["production_estimate"], "700.00")
        preview_prices = {item["key"]: item["price"] for item in preview["speed_options"]}
        self.assertEqual(preview_prices["faster"], "1050.00")
        self.assertEqual(preview_prices["fastest"], "1400.00")
        self.assertTrue(all(item["is_estimate"] for item in preview["speed_options"]))
        self.assertEqual(preview["speed_options"][1]["surcharge_percent"], FASTER_SURCHARGE_PERCENT)

        # 2) sent quote: the same surcharged production plus the same markup.
        partner = User.objects.create_user(
            email="law-partner@printy.test",
            password="pass12345",
            role="broker",
            partner_profile_enabled=True,
        )
        client_user = User.objects.create_user(
            email="law-client@printy.test", password="pass12345", role="client"
        )
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=partner,
            on_behalf_of=client_user,
            customer_name="Law Client",
            customer_email=client_user.email,
            status=QuoteStatus.DRAFT,
            request_snapshot={"source": "partner_quote_builder"},
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=partner,
            status=QuoteOfferStatus.PENDING,
            total=Decimal("700.00"),
            response_snapshot={"production_cost_inputs": productions_inputs},
        )
        api = APIClient()
        api.force_authenticate(user=partner)
        with patch("api.dashboard_views.create_payment_for_quote", return_value=object()):
            response = api.post(
                f"/api/dashboard/partner/quotes/{quote_request.id}/send-to-client/",
                {
                    "production_cost_inputs": productions_inputs,
                    "speed_tier": "faster",
                    "broker_margin_type": "fixed",
                    "broker_margin_value": "300.00",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        quote.refresh_from_db()
        self.assertEqual(response.data["pricing"]["final_client_price"], "1350.00")
        self.assertEqual(str(quote.total), "1350.00")
        # 3) final split: production is the surcharged amount, and the buyer pays
        #    exactly the preview total (1050 + 300 markup).
        self.assertEqual(str(quote.financial_split.production_cost), "1050.00")
        self.assertEqual(str(quote.financial_split.client_total), "1350.00")
        self.assertEqual(quote_request.speed_tier, "faster")
        self.assertEqual(quote_request.speed_multiplier, Decimal("1.50"))
        self.assertEqual(str(quote_request.production_uplift), "350.00")

    def test_configured_percentages_drive_the_multiplier(self):
        self.assertEqual(FASTER_SURCHARGE_PERCENT, 50)
        self.assertEqual(FASTEST_SURCHARGE_PERCENT, 100)
        self._priced_shop(standard_hours=40)
        self._opt_in("faster", "fastest")
        options = {item["tier"]["key"]: item for item in self._options()}

        self.assertEqual(options["faster"]["surcharge_percent"], 50)
        self.assertEqual(options["fastest"]["surcharge_percent"], 100)
        self.assertEqual(options["faster"]["multiplier"], "1.50")
        self.assertEqual(options["fastest"]["multiplier"], "2.00")

    # LAW 19 ----------------------------------------------------------------
    def test_rush_hours_derive_from_standard_and_can_be_overridden_or_switched_off(self):
        self._priced_shop(standard_hours=40)
        self._opt_in("faster", "fastest")

        options = {item["tier"]["key"]: item for item in self._options()}
        self.assertEqual(options["standard"]["turnaround_hours"], 40)
        self.assertEqual(options["faster"]["turnaround_hours"], derived_turnaround_hours("faster", 40))
        self.assertEqual(options["fastest"]["turnaround_hours"], derived_turnaround_hours("fastest", 40))

        self._opt_in("faster", hours=6)
        options = {item["tier"]["key"]: item for item in self._options()}
        self.assertEqual(options["faster"]["turnaround_hours"], 6)

        self._opt_in("fastest", active=False)
        keys = [item["tier"]["key"] for item in self._options()]
        self.assertEqual(keys, ["standard", "faster"])
        payload_keys = [item["key"] for item in buyer_speed_payload(options=self._options())["options"]]
        self.assertNotIn("fastest", payload_keys)

    # LAW 20 ----------------------------------------------------------------
    def test_ready_by_respects_business_hours_and_rolls_after_closing(self):
        self._priced_shop(standard_hours=10)
        self._opt_in("faster")  # 5 business hours
        after_close = datetime(2026, 11, 6, 19, 0, tzinfo=EAT)  # Friday 19:00, shop shut

        options = self._options(start=after_close)
        standard = select_speed_option(options=options, tier_key="standard")
        faster = select_speed_option(options=options, tier_key="faster")

        standard_ready = datetime.fromisoformat(standard["ready_by"]).astimezone(EAT)
        faster_ready = datetime.fromisoformat(faster["ready_by"]).astimezone(EAT)
        # 10 business hours from Friday 19:00 -> Monday 18:00; 5 -> Monday 13:00.
        self.assertEqual(standard_ready, datetime(2026, 11, 9, 18, 0, tzinfo=EAT))
        self.assertEqual(faster_ready, datetime(2026, 11, 9, 13, 0, tzinfo=EAT))
        for ready in (standard_ready, faster_ready):
            self.assertLess(ready.weekday(), 5)
            self.assertGreaterEqual(ready.hour, 8)
            self.assertLess(ready.hour, 18)
        self.assertTrue(standard["is_estimate"])
        self.assertTrue(faster["is_estimate"])
        payload = buyer_speed_payload(options=options)
        self.assertTrue(all(item["is_estimate"] for item in payload["options"]))

    def test_shop_opening_hours_are_used_not_the_platform_default(self):
        self.shop.opening_time = "10:00"
        self.shop.closing_time = "14:00"
        self.shop.save(update_fields=["opening_time", "closing_time"])
        self._priced_shop(standard_hours=8)
        self._opt_in("faster")  # 4 business hours

        options = self._options(start=datetime(2026, 11, 2, 11, 0, tzinfo=EAT))
        faster = select_speed_option(options=options, tier_key="faster")
        ready = datetime.fromisoformat(faster["ready_by"]).astimezone(EAT)

        # 11:00 + 4h within 10:00-14:00 -> next day 11:00.
        self.assertEqual(ready, datetime(2026, 11, 3, 11, 0, tzinfo=EAT))

    # LAW 21 ----------------------------------------------------------------
    def test_chosen_speed_sets_the_job_due_date_and_the_sla_reads_it(self):
        self._priced_shop(standard_hours=40)
        self._opt_in("fastest")
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.owner,
            customer_name="SLA Buyer",
            request_reference="QR-LAW-SLA",
        )
        job = ManagedJob.objects.create(
            title="Rush SLAs job",
            assigned_shop=self.shop,
            source_quote_request=quote_request,
            status="payment_confirmed",
        )
        option = select_speed_option(options=self._options(), tier_key="fastest")
        apply_speed_selection(quote_request=quote_request, option=option)
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            status=QuoteOfferStatus.ACCEPTED,
            accepted_at=monday(9),
            total=Decimal("2000.00"),
        )

        due = set_due_date_at_acceptance(quote=quote, actor=self.owner)

        quote_request.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(due, datetime.fromisoformat(option["ready_by"]))
        self.assertEqual(quote_request.due_date, due)
        self.assertEqual(job.due_date, due)
        self.assertEqual(job.speed_tier, "fastest")
        self.assertEqual(str(job.rush_premium), "1000.00")
        self.assertTrue(quote_request.speed_snapshot["locked"])

    # LAW 22 ----------------------------------------------------------------
    def test_fee_waiver_and_printer_side_fee_apply_to_the_surcharged_total(self):
        surcharged_base = Decimal("1500.00")
        markup = Decimal("500.00")

        waived = calculate_financial_split(
            production_cost=surcharged_base, manager_markup=markup, fee_waived=True
        )
        charged = calculate_financial_split(
            production_cost=surcharged_base, manager_markup=markup, fee_waived=False
        )

        self.assertEqual(waived.client_total, Decimal("2000.00"))
        self.assertEqual(waived.printy_fee, Decimal("0.00"))
        self.assertTrue(waived.fee_waived)
        self.assertGreaterEqual(waived.shop_payout, surcharged_base)
        self.assertEqual(waived.printer_side_fee, charged.printer_side_fee)
        self.assertGreater(charged.printy_fee, Decimal("0.00"))
        self.assertGreater(charged.printy_fee, Decimal("0.00"))
        self.assertGreaterEqual(charged.shop_payout, surcharged_base)
