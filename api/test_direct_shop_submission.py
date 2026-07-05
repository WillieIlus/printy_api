from decimal import Decimal

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from accounts.services.system_accounts import HOUSE_BROKER_EMAIL
from inventory.models import Machine, Paper
from jobs.models import JobAssignment, ManagedJob
from payments.models import Payment
from payments.services import mark_payment_paid
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from pricing.services.platform_fee_policy import calculate_financial_split
from quotes.models import CalculatorDraft, Quote, QuoteRequest
from shops.models import Shop


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    DIRECT_SHOP_STANDARD_MARKUP_RATE=Decimal("0.20"),
)
class DirectShopSubmissionTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client_user = User.objects.create_user(
            email="direct-submit-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Direct Client",
        )
        self.shop_owner = User.objects.create_user(
            email="direct-submit-shop@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
            name="Direct Shop Owner",
        )
        self.shop = self._create_shop_with_pricing()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Direct shop policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.10"),
            add_platform_fee_on_top=False,
        )

    def _create_shop_with_pricing(self):
        shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Direct Submit Shop",
            slug="direct-submit-shop",
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        machine = Machine.objects.create(
            shop=shop,
            name="Direct Submit Press",
            max_width_mm=320,
            max_height_mm=450,
            is_active=True,
        )
        Paper.objects.create(
            shop=shop,
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
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug="cutting-direct-submit-shop",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )
        return shop

    def _draft(self, *, user=None):
        draft = CalculatorDraft.objects.create(
            user=user or self.client_user,
            direct_intake_shop=self.shop,
            intake_mode=CalculatorDraft.INTAKE_MODE_DIRECT_SHOP,
            title="Direct shop job",
            calculator_inputs_snapshot={
                "product_type": "business_card",
                "quantity": 100,
                "width_mm": 85,
                "height_mm": 55,
                "paper_gsm": 300,
                "paper_type": "gloss",
                "print_sides": "SIMPLEX",
                "colour_mode": "COLOR",
            },
            request_details_snapshot={
                "source": "direct_shop_public_preview",
                "direct_shop_intake": True,
                "shop_id": self.shop.id,
                "shop_slug": self.shop.slug,
                "shop_name": self.shop.name,
                "customer_name": "Direct Client",
                "customer_email": "direct-submit-client@test.com",
            },
        )
        draft.draft_reference = f"QD-{draft.id}"
        draft.save(update_fields=["draft_reference", "updated_at"])
        return draft

    def _submit(self, draft, *, user=None):
        self.client.force_authenticate(user=user or self.client_user)
        return self.client.post(
            reverse("calculator-draft-direct-shop-submit", kwargs={"pk": draft.id}),
            {},
            format="json",
        )

    def _broker(self, email: str, *, active: bool) -> User:
        broker = User.objects.create_user(
            email=email,
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            name=email.split("@")[0],
        )
        UserProfile.objects.update_or_create(
            user=broker,
            defaults={"broker_profile_active": active},
        )
        return broker

    def _prior_brokered_job(self, *, broker: User):
        return ManagedJob.objects.create(
            client=self.client_user,
            broker=broker,
            created_by=broker,
            title="Prior brokered job",
        )

    def test_brokerless_client_creates_direct_shop_submission_and_payment_confirms_job(self):
        draft = self._draft()

        response = self._submit(draft)

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        payment = Payment.objects.get(pk=payload["payment"]["id"])
        quote = Quote.objects.get(pk=payload["quote_id"])
        quote_request = QuoteRequest.objects.get(pk=payload["quote_request_id"])
        self.assertEqual(quote_request.assigned_manager_id, self.shop_owner.id)
        self.assertEqual(quote.production_option.shop_id, self.shop.id)
        production_cost = quote.production_option.production_cost
        expected_broker_client_price = (production_cost * Decimal("1.20")).quantize(Decimal("0.01"))
        expected_split = calculate_financial_split(
            production_cost=production_cost,
            broker_client_price=expected_broker_client_price,
        )
        self.assertEqual(quote.total, expected_split["client_total"])
        self.assertEqual(quote.financial_split.broker_client_price, expected_broker_client_price)
        self.assertEqual(quote.financial_split.client_total, expected_split["client_total"])
        self.assertEqual(quote.financial_split.printy_fee, expected_split["printy_fee"])
        self.assertEqual(quote.financial_split.shop_payout, production_cost)
        self.assertEqual(quote.financial_split.broker_payout, expected_split["broker_payout"])
        self.assertGreater(quote.financial_split.broker_payout, Decimal("0.00"))
        self.assertEqual(
            quote.financial_split.shop_payout + quote.financial_split.broker_payout,
            quote.financial_split.client_total - quote.financial_split.printy_fee,
        )

        mark_payment_paid(payment)

        payment.refresh_from_db()
        managed_job = ManagedJob.objects.get(source_quote=quote)
        self.assertEqual(payment.managed_job_id, managed_job.id)
        self.assertEqual(managed_job.broker_id, self.shop_owner.id)
        self.assertEqual(managed_job.assigned_shop_id, self.shop.id)

    def test_preview_price_matches_submission_price_for_same_direct_shop_draft(self):
        draft = self._draft()
        preview_response = self.client.post(
            "/api/public/shops/direct-submit-shop/quote-preview/",
            draft.calculator_inputs_snapshot,
            format="json",
        )

        response = self._submit(draft)

        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(response.status_code, 201)
        quote = Quote.objects.get(pk=response.json()["quote_id"])
        self.assertEqual(Decimal(str(preview_response.json()["price"]["total"])), quote.financial_split.client_total)

    def test_active_broker_blocks_direct_shop_submission(self):
        broker = self._broker("active-existing-broker@test.com", active=True)
        self._prior_brokered_job(broker=broker)
        draft = self._draft()

        response = self._submit(draft)

        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertEqual(payload["code"], "existing_broker_required")
        self.assertEqual(payload["broker"]["id"], broker.id)
        self.assertEqual(payload["broker"]["is_printy_fallback"], False)
        self.assertEqual(payload["next_action"], "continue_with_broker")
        self.assertFalse(QuoteRequest.objects.filter(source_draft=draft).exists())
        self.assertEqual(Quote.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_inactive_prior_broker_blocks_with_house_broker_payload(self):
        inactive_broker = self._broker("inactive-existing-broker@test.com", active=False)
        self._prior_brokered_job(broker=inactive_broker)
        draft = self._draft()

        response = self._submit(draft)

        self.assertEqual(response.status_code, 409)
        payload = response.json()
        house_broker = User.objects.get(email=HOUSE_BROKER_EMAIL)
        self.assertEqual(payload["code"], "existing_broker_required")
        self.assertEqual(payload["broker"]["id"], house_broker.id)
        self.assertEqual(payload["broker"]["display_name"], "Printy")
        self.assertEqual(payload["broker"]["short_title"], "Managed by Printy")
        self.assertTrue(payload["broker"]["is_printy_fallback"])
        self.assertEqual(payload["broker"]["support_email"], "support@printy.ke")
        self.assertFalse(QuoteRequest.objects.filter(source_draft=draft).exists())
        self.assertEqual(Quote.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    DIRECT_SHOP_STANDARD_MARKUP_RATE=Decimal("0.20"),
)
class DirectShopPaymentAssignmentTransactionTestCase(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        self.client_user = User.objects.create_user(
            email="direct-assignment-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Direct Assignment Client",
        )
        self.shop_owner = User.objects.create_user(
            email="direct-assignment-shop@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
            name="Direct Assignment Shop Owner",
        )
        self.shop = self._create_shop_with_pricing()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Direct assignment policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.10"),
            add_platform_fee_on_top=False,
        )

    def _create_shop_with_pricing(self):
        shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Direct Assignment Shop",
            slug="direct-assignment-shop",
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        machine = Machine.objects.create(
            shop=shop,
            name="Direct Assignment Press",
            max_width_mm=320,
            max_height_mm=450,
            is_active=True,
        )
        Paper.objects.create(
            shop=shop,
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
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug="cutting-direct-assignment-shop",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )
        return shop

    def _draft(self):
        draft = CalculatorDraft.objects.create(
            user=self.client_user,
            direct_intake_shop=self.shop,
            intake_mode=CalculatorDraft.INTAKE_MODE_DIRECT_SHOP,
            title="Direct assignment job",
            calculator_inputs_snapshot={
                "product_type": "business_card",
                "quantity": 100,
                "width_mm": 85,
                "height_mm": 55,
                "paper_gsm": 300,
                "paper_type": "gloss",
                "print_sides": "SIMPLEX",
                "colour_mode": "COLOR",
            },
            request_details_snapshot={
                "source": "direct_shop_public_preview",
                "direct_shop_intake": True,
                "shop_id": self.shop.id,
                "shop_slug": self.shop.slug,
                "shop_name": self.shop.name,
                "customer_name": "Direct Assignment Client",
                "customer_email": "direct-assignment-client@test.com",
            },
        )
        draft.draft_reference = f"QD-{draft.id}"
        draft.save(update_fields=["draft_reference", "updated_at"])
        return draft

    def _submit(self, draft):
        self.client.force_authenticate(user=self.client_user)
        return self.client.post(
            reverse("calculator-draft-direct-shop-submit", kwargs={"pk": draft.id}),
            {},
            format="json",
        )

    def test_direct_shop_payment_success_creates_assignment_and_production_rows(self):
        response = self._submit(self._draft())
        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(pk=response.json()["payment"]["id"])
        quote = Quote.objects.get(pk=response.json()["quote_id"])

        mark_payment_paid(payment)
        mark_payment_paid(payment)

        payment.refresh_from_db()
        managed_job = ManagedJob.objects.get(source_quote=quote)
        self.assertEqual(payment.managed_job_id, managed_job.id)
        self.assertEqual(managed_job.assigned_shop_id, self.shop.id)
        self.assertEqual(
            JobAssignment.objects.filter(managed_job=managed_job, reassigned_from__isnull=True).count(),
            1,
        )
        assignment = JobAssignment.objects.get(managed_job=managed_job, reassigned_from__isnull=True)
        self.assertEqual(assignment.assigned_shop_id, self.shop.id)

        self.client.force_authenticate(user=self.shop_owner)
        home_response = self.client.get(reverse("dashboard-production-home"))
        self.assertEqual(home_response.status_code, 200)
        home_payload = home_response.json()
        self.assertEqual(home_payload["stats"]["incoming_assignments"], 1)
        self.assertEqual(home_payload["assignments"][0]["id"], assignment.id)
        self.assertEqual(home_payload["queue"][0]["source"], "direct_shop")

        list_response = self.client.get(reverse("dashboard-production-jobs"))
        self.assertEqual(list_response.status_code, 200)
        row = list_response.json()["results"][0]
        self.assertEqual(row["id"], managed_job.id)
        self.assertEqual(row["source"], "direct_shop")
