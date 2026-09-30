"""KES whole-shilling rounding: one rounding point, no truncation at the STK site.

The failure this file exists to prevent
---------------------------------------
A quote priced at 2116.66 with 75% markup produced a client_total with cents.
The client saw the cents figure, the Daraja call site truncated the charge to
whole shillings with ``int()``, and the callback's amount-mismatch guard then
compared the truncated charge against the un-truncated expectation. The
customer was under-charged and the payment then hard-failed reconciliation.

The fix is that money is rounded **once**, to whole KES with ROUND_HALF_UP, in
``pricing.services.platform_fee_policy.calculate_quote_financials`` — and that
integer is what gets stored, published, charged and reconciled. These tests pin
all four properties end to end:

  (a) client_total is a whole KES integer
  (b) the STK amount matches client_total exactly (no truncation, no rounding)
  (c) the split components sum to client_total exactly (residual printy_fee)
  (d) the callback mismatch guard accepts the confirmed amount
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from common.money import (
    is_whole_kes,
    money,
    require_whole_kes,
    to_decimal,
    whole_kes,
)
from mpesa_payments.models import MpesaPayment, MpesaPaymentStatus
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import (
    calculate_financial_split,
    calculate_quote_financials,
)

PRODUCTION_COST = Decimal("2116.66")
MARKUP_RATE = Decimal("0.75")


class RoundingRuleTests(SimpleTestCase):
    """The one rounding rule, in isolation."""

    def test_whole_kes_is_half_up(self):
        self.assertEqual(whole_kes("2500.50"), Decimal("2501"))
        self.assertEqual(whole_kes("2500.49"), Decimal("2500"))
        self.assertEqual(whole_kes("2116.66"), Decimal("2117"))
        self.assertEqual(whole_kes("-0.50"), Decimal("-1"))

    def test_whole_kes_never_truncates(self):
        # int() would give a lower charge for every one of these. Truncation
        # and rounding only differ once the fraction reaches half a shilling.
        for value in ("0.99", "1.50", "1.99", "2.50", "99.99", "2500.99", "2116.66"):
            self.assertGreater(
                whole_kes(value),
                Decimal(int(Decimal(value))),
                f"whole_kes({value}) truncated instead of rounding",
            )

    def test_whole_kes_agrees_with_truncation_below_half(self):
        # Below half a shilling there is nothing to round, so the two agree.
        for value in ("0.01", "1.01", "1.49", "99.49", "2500.49"):
            self.assertEqual(whole_kes(value), Decimal(int(Decimal(value))))

    def test_whole_kes_is_not_bankers_rounding(self):
        # Banker's rounding sends exactly-half values to even: 2500.50 -> 2500.
        # That inconsistency is why every money module now shares this helper.
        self.assertNotEqual(whole_kes("2500.50"), Decimal("2500"))
        self.assertNotEqual(whole_kes("0.50"), Decimal("0"))

    def test_whole_kes_output_is_integral(self):
        for value in ("0.01", "2116.66", "99999.49", "1.00"):
            self.assertTrue(is_whole_kes(whole_kes(value)))

    def test_money_stays_two_dp_for_rates_and_costs(self):
        self.assertEqual(money("2116.665"), Decimal("2116.67"))
        self.assertEqual(money("0.125"), Decimal("0.13"))

    def test_float_input_does_not_import_binary_error(self):
        # Decimal(0.1) is 0.1000000000000000055511151231; str() avoids that.
        self.assertEqual(whole_kes(0.1), Decimal("0"))
        self.assertEqual(money(0.1), Decimal("0.10"))

    def test_require_whole_kes_rejects_cents(self):
        with self.assertRaises(ValueError) as ctx:
            require_whole_kes("2116.66", "STK amount")
        self.assertIn("whole KES", str(ctx.exception))

    def test_require_whole_kes_accepts_integral_forms(self):
        for value in ("2117", "2117.00", Decimal("2117"), 2117):
            self.assertEqual(require_whole_kes(value), Decimal("2117"))

    def test_to_decimal_handles_none_and_empty(self):
        self.assertEqual(to_decimal(None), Decimal("0"))
        self.assertEqual(to_decimal(""), Decimal("0"))


class PromptScenarioSplitTests(SimpleTestCase):
    """production cost 2116.66, markup 75% — the case from the brief."""

    def setUp(self):
        self.policy = PlatformFeePolicy()
        self.split = calculate_financial_split(
            production_cost=PRODUCTION_COST,
            manager_markup=PRODUCTION_COST * MARKUP_RATE,
            policy=self.policy,
        )

    def test_a_every_published_amount_is_a_whole_kes_integer(self):
        for field in (
            "production_cost",
            "manager_markup",
            "broker_client_price",
            "client_total",
            "production_fee_component",
            "printer_side_fee",
            "printy_fee",
            "markup_fee_component",
            "shop_payout",
            "manager_payout",
            "broker_payout",
            "gross_margin",
        ):
            value = self.split[field]
            self.assertTrue(
                is_whole_kes(value),
                f"{field} is {value!r}, which is not whole KES",
            )
            # A whole number has no fractional part and a zero exponent, so
            # nothing in the chain is still carrying cents.
            self.assertEqual(value.as_tuple().exponent, 0, f"{field} kept cents")

    def test_production_cost_rounds_half_up(self):
        self.assertEqual(self.split["production_cost"], Decimal("2117"))

    def test_client_total_is_cost_plus_rounded_markup(self):
        self.assertEqual(self.split["client_total"], Decimal("3704"))
        self.assertEqual(
            self.split["client_total"],
            self.split["production_cost"] + self.split["manager_markup"],
        )

    def test_c_split_components_sum_exactly_to_client_total(self):
        components = (
            self.split["shop_payout"],
            self.split["manager_payout"],
            self.split["printy_fee"],
        )
        self.assertEqual(sum(components), self.split["client_total"])
        # Exact equality, not a tolerance: this is the rounding-split bug guard.
        self.assertEqual(sum(components) - self.split["client_total"], Decimal("0"))

    def test_printy_fee_is_the_residual(self):
        self.assertEqual(
            self.split["printy_fee"],
            self.split["client_total"]
            - self.split["shop_payout"]
            - self.split["manager_payout"],
        )

    def test_aliases_stay_consistent(self):
        self.assertEqual(self.split["broker_payout"], self.split["manager_payout"])
        self.assertEqual(self.split["markup_fee_component"], self.split["printy_fee"])
        self.assertEqual(
            self.split["printer_side_fee"], self.split["production_fee_component"]
        )
        self.assertEqual(self.split["gross_margin"], self.split["manager_markup"])
        self.assertEqual(self.split["broker_client_price"], self.split["client_total"])

    def test_rates_stay_fractional(self):
        # The markup multiple is a rate, not money, so it keeps 4dp.
        self.assertEqual(self.split["applied_markup_multiple"].as_tuple().exponent, -4)

    def test_split_is_deterministic(self):
        again = calculate_financial_split(
            production_cost=PRODUCTION_COST,
            manager_markup=PRODUCTION_COST * MARKUP_RATE,
            policy=self.policy,
        )
        for field in ("client_total", "shop_payout", "manager_payout", "printy_fee"):
            self.assertEqual(again[field], self.split[field], field)


class RoundingSplitRegressionTests(SimpleTestCase):
    """The components must reconcile for every input, not just the happy path."""

    def _assert_reconciles(self, production_cost, manager_markup):
        result = calculate_quote_financials(
            production_cost=production_cost,
            manager_markup=manager_markup,
            policy=PlatformFeePolicy(),
        )
        total = result.shop_payout + result.manager_payout + result.printy_fee
        self.assertEqual(
            total,
            result.client_total,
            f"split failed to reconcile for cost={production_cost} markup={manager_markup}: "
            f"shop={result.shop_payout} manager={result.manager_payout} "
            f"printy={result.printy_fee} total={result.client_total}",
        )
        for value in (result.client_total, result.shop_payout, result.manager_payout, result.printy_fee):
            self.assertTrue(is_whole_kes(value))
        return result

    def test_exhaustive_reconciliation_across_cents_and_tiers(self):
        # Sweep the values that used to break the split: half-shillings in the
        # cost, in the markup, and in both, across all three pricing tiers.
        from django.core.exceptions import ValidationError

        checked = 0
        for cost in ("0.50", "1.00", "999.50", "1000.50", "2116.66", "4724.33", "10000.50", "10001.00"):
            for markup in ("0.50", "1.50", "7.49", "99.50", "750.00", "1587.495"):
                try:
                    self._assert_reconciles(Decimal(cost), Decimal(markup))
                    checked += 1
                except ValidationError:
                    continue  # above the tier cap; rejected by design
        self.assertGreater(checked, 20, "sweep exercised almost nothing")

    def test_half_shilling_markup_does_not_leak_a_cent_into_the_split(self):
        result = self._assert_reconciles(Decimal("1000.00"), Decimal("750.00"))
        # 750 * 0.45 = 337.50, which ROUND_HALF_UP sends to 338. The extra
        # half shilling is absorbed by printy_fee rather than breaking the sum.
        self.assertEqual(result.manager_payout, Decimal("338"))
        self.assertEqual(result.printy_fee, Decimal("362"))

    def test_broker_client_price_entry_point_also_rounds_once(self):
        # Callers may pass a client price with cents instead of a markup.
        result = calculate_financial_split(
            production_cost=Decimal("2116.66"),
            broker_client_price=Decimal("3704.99"),
            policy=PlatformFeePolicy(),
        )
        self.assertEqual(result.client_total, Decimal("3705"))
        self.assertEqual(result.manager_markup, Decimal("1588"))
        self.assertEqual(
            result.shop_payout + result.manager_payout + result.printy_fee,
            result.client_total,
        )

    def test_negative_input_is_rejected_before_rounding(self):
        # -0.01 rounds to 0, so validation must run on the raw value or a
        # negative would slip through as a legitimate zero.
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            calculate_quote_financials(
                production_cost=Decimal("1000.00"),
                manager_markup=Decimal("-0.01"),
                policy=PlatformFeePolicy(),
            )

    def test_sub_shilling_cost_is_rejected(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            calculate_quote_financials(
                production_cost=Decimal("0.40"),
                manager_markup=Decimal("10.00"),
                policy=PlatformFeePolicy(),
            )


class StkAmountIsNotTruncatedTests(TestCase):
    """Requirement 5: nothing is rounded at the Daraja call site.

    Before the fix both clients cast with ``int()``, so 3704.99 would have been
    charged as 3704. Now the amount must already be whole, and a cent-carrying
    amount is a loud failure rather than a silent under-charge.
    """

    def setUp(self):
        self.payment = MpesaPayment.objects.create(
            phone_number="254712345678",
            amount=Decimal("3704"),
            account_reference="PRINTY",
            description="Printy payment",
        )

    def test_mpesa_payments_client_rejects_a_cent_carrying_amount(self):
        from django.test import override_settings

        with override_settings(
            MPESA_ENV="sandbox",
            MPESA_SHORTCODE_TYPE="paybill",
            MPESA_SHORTCODE="174379",
            MPESA_PASSKEY="passkey",
            MPESA_CALLBACK_URL="https://api.printy.ke/api/payments/mpesa/callback/",
        ):
            with patch("mpesa_payments.services.get_access_token", return_value="tok"):
                with patch("mpesa_payments.services.requests.post") as post:
                    self.payment.amount = Decimal("3704.99")
                    self.payment.save(update_fields=["amount"])
                    with self.assertRaises(ValueError) as ctx:
                        from mpesa_payments.services import initiate_stk_push

                        initiate_stk_push(self.payment)
                    self.assertIn("whole KES", str(ctx.exception))
                    post.assert_not_called()

    def test_mpesa_payments_client_sends_the_exact_whole_amount(self):
        from django.test import override_settings

        with override_settings(
            MPESA_ENV="sandbox",
            MPESA_SHORTCODE_TYPE="paybill",
            MPESA_SHORTCODE="174379",
            MPESA_PASSKEY="passkey",
            MPESA_CALLBACK_URL="https://api.printy.ke/api/payments/mpesa/callback/",
        ):
            with patch("mpesa_payments.services.get_access_token", return_value="tok"):
                with patch("mpesa_payments.services.requests.post") as post:
                    post.return_value = _resp(
                        200,
                        {
                            "CheckoutRequestID": "ws_CO_1",
                            "MerchantRequestID": "mr_1",
                            "ResponseCode": "0",
                            "ResponseDescription": "Success",
                        },
                    )
                    from mpesa_payments.services import initiate_stk_push

                    initiate_stk_push(self.payment)
                    self.assertEqual(post.call_args[1]["json"]["Amount"], 3704)

    def test_daraja_client_rejects_a_cent_carrying_amount(self):
        from payments.services import MpesaDarajaClient

        client = MpesaDarajaClient()
        with patch.object(client, "get_access_token", return_value="tok"):
            with self.assertRaises(ValueError) as ctx:
                client.initiate_stk_push(
                    phone_number="254712345678",
                    amount=Decimal("3704.99"),
                    account_reference="PRINTY",
                    transaction_desc="Printy payment",
                )
        self.assertIn("whole KES", str(ctx.exception))

    def test_daraja_client_sends_the_exact_whole_amount(self):
        from payments.services import MpesaDarajaClient

        client = MpesaDarajaClient()
        with patch.object(client, "get_access_token", return_value="tok"):
            with patch("payments.services.requests.post") as post:
                post.return_value = _resp(200, {"ResponseCode": "0"})
                client.initiate_stk_push(
                    phone_number="254712345678",
                    amount=Decimal("3704"),
                    account_reference="PRINTY",
                    transaction_desc="Printy payment",
                )
                self.assertEqual(post.call_args[1]["json"]["Amount"], 3704)


class CallbackMismatchGuardTests(TestCase):
    """Requirement (d): the guard accepts the confirmed amount, no false mismatch."""

    def _payment(self, amount="3704"):
        return MpesaPayment.objects.create(
            phone_number="254712345678",
            amount=Decimal(amount),
            account_reference="PRINTY",
            description="Printy payment",
        )

    def test_confirmed_whole_amount_matches_and_pays(self):
        payment = self._payment()
        payment.mark_push_sent(merchant_request_id="mr", checkout_request_id="ws_CO_9")

        changed = payment.mark_confirmed(
            receipt_number="QG1234XY",
            paid_amount=Decimal("3704.00"),
            result_code="0",
            result_desc="Success",
        )

        self.assertTrue(changed)
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.reconciliation_status, "confirmed")

    def test_integer_and_two_dp_forms_of_the_same_amount_are_equal(self):
        payment = self._payment()
        payment.mark_push_sent(merchant_request_id="mr", checkout_request_id="ws_CO_10")

        payment.mark_confirmed(receipt_number="QG1234XY", paid_amount=Decimal("3704"))

        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.paid_amount, Decimal("3704.00"))

    def test_a_real_mismatch_still_needs_review(self):
        # Guards must not be loosened into rubber-stamping wrong amounts.
        payment = self._payment()
        payment.mark_push_sent(merchant_request_id="mr", checkout_request_id="ws_CO_11")

        payment.mark_confirmed(receipt_number="QG1234XY", paid_amount=Decimal("3703"))

        self.assertEqual(payment.status, MpesaPaymentStatus.NEEDS_REVIEW)
        self.assertEqual(payment.reconciliation_status, "amount_mismatch")

    def test_stk_amount_equals_client_total_for_the_prompt_scenario(self):
        split = calculate_financial_split(
            production_cost=PRODUCTION_COST,
            manager_markup=PRODUCTION_COST * MARKUP_RATE,
            policy=PlatformFeePolicy(),
        )
        payment = self._payment(str(split.client_total))
        payment.mark_push_sent(merchant_request_id="mr", checkout_request_id="ws_CO_12")

        # This is what Daraja was actually asked to charge.
        charged = require_whole_kes(split.client_total, "STK amount")
        # ...and this is what it reports back in the callback.
        payment.mark_confirmed(receipt_number="QG1234XY", paid_amount=Decimal(charged))

        self.assertEqual(charged, int(split.client_total))
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.reconciliation_status, "confirmed")


def _resp(status_code: int, payload: dict):
    from unittest.mock import MagicMock

    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response
