from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from jobs.artwork_confirmation import request_client_artwork_confirmation
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob
from jobs.services.dispatch import dispatch_job_to_shop
from pricing.models import PlatformFeePolicy
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import ProductionOption, Quote, QuoteFinancialSplit, QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class Phase9ArtworkConfirmationTestCase(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.client_user = User.objects.create_user(
            email="phase9-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.other_client = User.objects.create_user(
            email="phase9-other-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="phase9-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.other_manager = User.objects.create_user(
            email="phase9-other-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.shop_owner = User.objects.create_user(
            email="phase9-shop@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Phase 9 Shop",
            slug="phase-9-shop",
            is_active=True,
        )
        self.policy = PlatformFeePolicy.objects.create(
            name="Phase 9 policy",
            printer_fee_rate=Decimal("0.0000"),
            broker_margin_fee_rate=Decimal("0.0000"),
            is_active=True,
        )
        self.managed_job = self._managed_job(suffix="primary")

    def _managed_job(self, *, suffix: str) -> ManagedJob:
        quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name=f"Phase 9 Client {suffix}",
            customer_email=self.client_user.email,
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
            title=f"Phase 9 job {suffix}",
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

    def _request_confirmation(self, user=None):
        self.api_client.force_authenticate(user=user or self.manager)
        return self.api_client.post(
            f"/api/managed-jobs/{self.managed_job.id}/artwork-confirmation/request/",
            {"note": "Please confirm the final artwork."},
            format="json",
        )

    def test_manager_can_request_client_artwork_confirmation(self):
        response = self._request_confirmation()

        self.assertEqual(response.status_code, 200)
        payload = response.json()["artwork_confirmation"]
        self.assertEqual(payload["state"], "requested")
        self.assertEqual(payload["requested_by"], self.manager.id)

        self.managed_job.refresh_from_db()
        self.assertEqual(self.managed_job.workflow_metadata["artwork_confirmation"]["state"], "requested")

    def test_unrelated_manager_cannot_request_confirmation(self):
        response = self._request_confirmation(user=self.other_manager)

        self.assertEqual(response.status_code, 403)
        self.managed_job.refresh_from_db()
        self.assertNotIn("artwork_confirmation", self.managed_job.workflow_metadata)

    def test_client_can_approve_requested_confirmation(self):
        self._request_confirmation()
        self.api_client.force_authenticate(user=self.client_user)

        response = self.api_client.post(
            f"/api/managed-jobs/{self.managed_job.id}/artwork-confirmation/respond/",
            {"action": "approve", "note": "Approved."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["artwork_confirmation"]["state"], "approved")

    def test_unrelated_client_cannot_respond_to_confirmation(self):
        self._request_confirmation()
        self.api_client.force_authenticate(user=self.other_client)

        response = self.api_client.post(
            f"/api/managed-jobs/{self.managed_job.id}/artwork-confirmation/respond/",
            {"action": "approve"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.managed_job.refresh_from_db()
        self.assertEqual(self.managed_job.workflow_metadata["artwork_confirmation"]["state"], "requested")

    def test_requested_confirmation_blocks_dispatch(self):
        request_client_artwork_confirmation(managed_job=self.managed_job, actor=self.manager)

        with self.assertRaisesMessage(ValidationError, "Client artwork confirmation is required before dispatch."):
            dispatch_job_to_shop(managed_job=self.managed_job, dispatched_by=self.manager)

        self.assertFalse(JobAssignment.objects.filter(managed_job=self.managed_job).exists())

    def test_approved_confirmation_allows_paid_job_dispatch(self):
        request_client_artwork_confirmation(managed_job=self.managed_job, actor=self.manager)
        self.api_client.force_authenticate(user=self.client_user)
        response = self.api_client.post(
            f"/api/managed-jobs/{self.managed_job.id}/artwork-confirmation/respond/",
            {"action": "approve"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        assignment = dispatch_job_to_shop(managed_job=self.managed_job, dispatched_by=self.manager)

        self.assertEqual(assignment.managed_job_id, self.managed_job.id)
        self.managed_job.refresh_from_db()
        self.assertIsNotNone(self.managed_job.dispatched_at)

    def test_public_tracking_does_not_expose_confirmation_or_file_urls(self):
        request_client_artwork_confirmation(managed_job=self.managed_job, actor=self.manager)

        response = self.api_client.get(f"/api/public/managed-jobs/track/{self.managed_job.tracking_token}/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("artwork_confirmation", payload)
        self.assertNotIn("files", payload)
        self.assertNotIn("attachments", payload)
        self.assertFalse(any("download_url" in str(value) for value in payload.values()))
