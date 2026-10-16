"""Law 7.2 artwork-is-a-workflow-step and law 7.7 track-job lookups."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from jobs.artwork_workflow import (
    PAYMENT_BLOCK_AWAITING_MANAGER,
    PAYMENT_BLOCK_UPLOAD,
    artwork_payment_block_reason,
    artwork_satisfied,
)
from jobs.choices import JobFileType, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, JobEvent, JobFile, ManagedJob
from jobs.services.dispatch import dispatch_job_to_shop
from pricing.models import PlatformFeePolicy
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import ProductionOption, Quote, QuoteFinancialSplit, QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ArtworkWorkflowLaw72TestCase(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.client_user = User.objects.create_user(
            email="law72-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.other_client = User.objects.create_user(
            email="law72-other-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="law72-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.other_manager = User.objects.create_user(
            email="law72-other-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.shop_owner = User.objects.create_user(
            email="law72-shop@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Law 72 Shop",
            slug="law-72-shop",
            is_active=True,
        )
        self.policy = PlatformFeePolicy.objects.create(
            name="Law 72 policy",
            printer_fee_rate=Decimal("0.0000"),
            broker_margin_fee_rate=Decimal("0.0000"),
            is_active=True,
        )
        self.managed_job = self._managed_job()

    def _managed_job(self) -> ManagedJob:
        quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Law 72 Client",
            customer_email=self.client_user.email,
            customer_phone="+254711222333",
            status=QuoteStatus.CLOSED,
        )
        production_option = ProductionOption.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.manager,
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            production_option=production_option,
            created_by=self.manager,
            status=QuoteOfferStatus.ACCEPTED,
            total=Decimal("1500.00"),
        )
        QuoteFinancialSplit.objects.create(
            quote=quote,
            policy_used=self.policy,
            production_option=production_option,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            gross_margin=Decimal("500.00"),
            printer_side_fee=Decimal("0.00"),
            broker_margin_fee=Decimal("0.00"),
            printy_fee=Decimal("0.00"),
            shop_payout=Decimal("1000.00"),
            broker_payout=Decimal("500.00"),
            client_total=Decimal("1500.00"),
            max_allowed_client_price=Decimal("5000.00"),
            applied_markup_multiple=Decimal("1.5000"),
        )
        return ManagedJob.objects.create(
            title="Law 72 job",
            source_quote_request=quote_request,
            source_quote=quote,
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=Decimal("1500.00"),
            printy_fee=Decimal("0.00"),
            broker_payout=Decimal("500.00"),
        )

    def _workflow(self, user, action, **payload):
        self.api_client.force_authenticate(user=user)
        return self.api_client.post(
            f"/api/managed-jobs/{self.managed_job.id}/artwork/workflow/",
            {"action": action, **payload},
            format="json",
        )

    def _stk_push(self, user=None):
        self.api_client.force_authenticate(user=user or self.client_user)
        return self.api_client.post(
            f"/api/managed-jobs/{self.managed_job.id}/payments/mpesa/stk-push/",
            {"phone_number": "+254700000000"},
            format="json",
        )

    def _upload_artwork(self):
        JobFile.objects.create(
            managed_job=self.managed_job,
            uploaded_by=self.client_user,
            file_type=JobFileType.ARTWORK,
            original_filename="artwork.pdf",
        )

    # --- payment initiation gate (law 7.2) --------------------------------

    def test_payment_initiation_blocked_without_artwork(self):
        response = self._stk_push()

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], PAYMENT_BLOCK_UPLOAD)
        self.assertEqual(body["artwork_payment_block_reason"], PAYMENT_BLOCK_UPLOAD)

    def test_payment_initiation_blocked_while_manager_acceptance_pending(self):
        self._workflow(self.client_user, "claim")

        response = self._stk_push()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], PAYMENT_BLOCK_AWAITING_MANAGER)

    def test_uploaded_artwork_satisfies_and_clears_block(self):
        self._upload_artwork()

        self.assertTrue(artwork_satisfied(self.managed_job))
        self.assertIsNone(artwork_payment_block_reason(self.managed_job))
        response = self._stk_push()
        # Past the artwork gate: no pending payment exists yet.
        self.assertEqual(response.json()["detail"], "No pending or processing payment found for this managed job.")

    # --- buyer claim / manager accept (law 7.2) ---------------------------

    def test_manager_accept_clears_payment_block(self):
        self._workflow(self.client_user, "claim")
        response = self._workflow(self.manager, "manager_accept")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["artwork_workflow"]
        self.assertEqual(payload["claim_state"], "accepted")
        self.assertTrue(payload["satisfied"])
        self.assertIsNone(payload["payment_block_reason"])
        self.managed_job.refresh_from_db()
        self.assertTrue(artwork_satisfied(self.managed_job))

    def test_manager_decline_returns_buyer_to_upload_block(self):
        self._workflow(self.client_user, "claim")
        response = self._workflow(self.manager, "manager_decline", reason="No artwork on file.")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["artwork_workflow"]["claim_state"], "declined")
        self.assertEqual(artwork_payment_block_reason(self.managed_job), PAYMENT_BLOCK_UPLOAD)

    def test_manager_can_mark_holds_without_buyer_claim(self):
        response = self._workflow(self.manager, "manager_holds")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["artwork_workflow"]
        self.assertEqual(payload["claim_state"], "accepted")
        self.assertTrue(payload["satisfied"])

    # --- role authorization -------------------------------------------------

    def test_unrelated_client_cannot_claim(self):
        response = self._workflow(self.other_client, "claim")
        self.assertEqual(response.status_code, 403)

    def test_unrelated_manager_cannot_accept(self):
        self._workflow(self.client_user, "claim")
        response = self._workflow(self.other_manager, "manager_accept")
        self.assertEqual(response.status_code, 403)

    def test_buyer_cannot_accept_own_claim(self):
        self._workflow(self.client_user, "claim")
        response = self._workflow(self.client_user, "manager_accept")
        self.assertEqual(response.status_code, 403)

    # --- stage owner (law 6.4) ---------------------------------------------

    def test_stage_owner_is_buyer_then_manager(self):
        from jobs.artwork_workflow import get_artwork_workflow_payload

        self.assertEqual(get_artwork_workflow_payload(self.managed_job)["stage_owner"], "buyer")

        self._workflow(self.client_user, "claim")
        self.managed_job.refresh_from_db()
        self.assertEqual(get_artwork_workflow_payload(self.managed_job)["stage_owner"], "manager")

    # --- printer acknowledgement gates production (law 7.2) ---------------

    def test_manager_holds_dispatch_requires_printer_acknowledgement(self):
        self._workflow(self.manager, "manager_holds")

        with self.assertRaisesMessage(
            ValidationError,
            "The printer must acknowledge they hold the artwork before production can start.",
        ):
            dispatch_job_to_shop(managed_job=self.managed_job, dispatched_by=self.manager)
        self.assertFalse(JobAssignment.objects.filter(managed_job=self.managed_job).exists())

        ack = self._workflow(self.shop_owner, "printer_acknowledge")
        self.assertEqual(ack.status_code, 200)
        self.assertIsNotNone(ack.json()["artwork_workflow"]["printer_acknowledged_at"])

        assignment = dispatch_job_to_shop(managed_job=self.managed_job, dispatched_by=self.manager)
        self.assertEqual(assignment.managed_job_id, self.managed_job.id)

    def test_uploaded_artwork_dispatch_needs_no_printer_acknowledgement(self):
        self._upload_artwork()
        assignment = dispatch_job_to_shop(managed_job=self.managed_job, dispatched_by=self.manager)
        self.assertEqual(assignment.managed_job_id, self.managed_job.id)

    # --- event log (law 6.3) -----------------------------------------------

    def test_workflow_actions_are_written_to_the_event_log(self):
        self._workflow(self.client_user, "claim")
        self._workflow(self.manager, "manager_accept")
        self._workflow(self.shop_owner, "printer_acknowledge")

        events = set(
            JobEvent.objects.filter(managed_job=self.managed_job).values_list("event", flat=True)
        )
        self.assertIn("artwork_claimed_with_manager", events)
        self.assertIn("artwork_manager_accepted", events)
        self.assertIn("artwork_printer_acknowledged", events)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PublicTrackJobLookupLaw77TestCase(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.client_user = User.objects.create_user(
            email="law77-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="law77-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.shop_owner = User.objects.create_user(
            email="law77-shop@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Law 77 Shop",
            slug="law-77-shop",
            is_active=True,
        )
        quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Law 77 Client",
            customer_email="buyer-contact@example.com",
            customer_phone="+254733444555",
            status=QuoteStatus.CLOSED,
        )
        self.managed_job = ManagedJob.objects.create(
            title="Law 77 job",
            source_quote_request=quote_request,
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=Decimal("1500.00"),
        )

    def _track(self, token, **params):
        return self.api_client.get(f"/api/public/managed-jobs/track/{token}/", params)

    def test_token_lookup_needs_no_contact(self):
        response = self._track(self.managed_job.tracking_token)
        self.assertEqual(response.status_code, 200)

    def test_reference_lookup_requires_contact(self):
        response = self._track(self.managed_job.managed_reference)
        self.assertEqual(response.status_code, 400)

    def test_reference_lookup_with_matching_email_succeeds(self):
        response = self._track(self.managed_job.managed_reference, email="buyer-contact@example.com")
        self.assertEqual(response.status_code, 200)

    def test_reference_lookup_with_matching_phone_succeeds(self):
        response = self._track(self.managed_job.managed_reference, phone="0733444555")
        self.assertEqual(response.status_code, 200)

    def test_reference_lookup_with_wrong_contact_is_rejected(self):
        response = self._track(self.managed_job.managed_reference, email="stranger@example.com")
        self.assertEqual(response.status_code, 400)

    def test_public_payload_exposes_no_prices(self):
        response = self._track(self.managed_job.tracking_token)
        body = response.json()
        for forbidden in ("client_total", "printy_fee", "broker_payout", "payout_hold", "pricing"):
            self.assertNotIn(forbidden, body)
