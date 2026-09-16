from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from api.services.actor_serializer import select_actor_serializer
from jobs.choices import JobFileStatus, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.file_services import upload_proof_for_managed_job
from jobs.managed_job_actor_serializers import ManagedJobBrokerSerializer, ManagedJobShopSerializer
from jobs.models import JobAssignment, ManagedJob
from pricing.models import PlatformFeePolicy
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import ProductionOption, Quote, QuoteFinancialSplit, QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ShopAsManagerAccessTestCase(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.client_user = User.objects.create_user(
            email="shop-manager-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.shop_owner = User.objects.create_user(
            email="shop-manager-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.other_broker = User.objects.create_user(
            email="shop-manager-other-broker@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Shop Manager Access",
            slug="shop-manager-access",
            is_active=True,
        )
        self.policy = PlatformFeePolicy.objects.create(
            name="Shop Manager Policy",
            printer_fee_rate=Decimal("0.0000"),
            broker_margin_fee_rate=Decimal("0.0000"),
            is_active=True,
        )
        self.direct_job = self._managed_job(
            broker=self.shop_owner,
            suffix="direct",
            client_total=Decimal("1500.00"),
            printy_fee=Decimal("225.00"),
            broker_payout=Decimal("225.00"),
            shop_payout=Decimal("1050.00"),
        )
        self.shop_only_job = self._managed_job(
            broker=self.other_broker,
            suffix="shop-only",
            client_total=Decimal("1400.00"),
            printy_fee=Decimal("170.00"),
            broker_payout=Decimal("180.00"),
            shop_payout=Decimal("1050.00"),
        )

    def _managed_job(
        self,
        *,
        broker,
        suffix: str,
        client_total: Decimal,
        printy_fee: Decimal,
        broker_payout: Decimal,
        shop_payout: Decimal,
    ) -> ManagedJob:
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            assigned_manager=broker,
            customer_name=f"Client {suffix}",
            customer_email=self.client_user.email,
            status=QuoteStatus.CLOSED,
        )
        production_option = ProductionOption.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=broker,
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            production_option=production_option,
            created_by=broker,
            status=QuoteOfferStatus.ACCEPTED,
            total=client_total,
        )
        QuoteFinancialSplit.objects.create(
            quote=quote,
            policy_used=self.policy,
            production_option=production_option,
            production_cost=Decimal("1000.00"),
            broker_client_price=client_total,
            gross_margin=client_total - Decimal("1000.00"),
            printer_side_fee=Decimal("0.00"),
            broker_margin_fee=printy_fee,
            printy_fee=printy_fee,
            shop_payout=shop_payout,
            broker_payout=broker_payout,
            client_total=client_total,
            max_allowed_client_price=Decimal("5000.00"),
            applied_markup_multiple=Decimal("1.5000"),
        )
        managed_job = ManagedJob.objects.create(
            title=f"Shop as manager {suffix}",
            source_quote_request=quote_request,
            source_quote=quote,
            client=self.client_user,
            broker=broker,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=client_total,
            printy_fee=printy_fee,
            broker_payout=broker_payout,
        )
        JobAssignment.objects.create(
            managed_job=managed_job,
            assigned_shop=self.shop,
            source_quote=quote,
            status="accepted",
            shop_payout=shop_payout,
        )
        return managed_job

    def _upload_proof(self, managed_job: ManagedJob):
        return upload_proof_for_managed_job(
            managed_job=managed_job,
            assignment=managed_job.assignments.filter(reassigned_from__isnull=True).first(),
            uploaded_by=self.shop_owner,
            file=SimpleUploadedFile(f"proof-{managed_job.id}.pdf", b"proof", content_type="application/pdf"),
            original_filename=f"proof-{managed_job.id}.pdf",
        )

    def test_specific_managed_job_serializer_is_broker_for_broker_of_record_only(self):
        direct_serializer = select_actor_serializer("managed_job", self.shop_owner, instance=self.direct_job)
        shop_only_serializer = select_actor_serializer("managed_job", self.shop_owner, instance=self.shop_only_job)

        self.assertIs(direct_serializer, ManagedJobBrokerSerializer)
        self.assertIs(shop_only_serializer, ManagedJobShopSerializer)

    def test_managed_job_list_uses_object_aware_projection_per_row(self):
        self.api_client.force_authenticate(user=self.shop_owner)

        response = self.api_client.get("/api/managed-jobs/")

        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.json()}
        self.assertIn("sourced_shop_identity", rows[self.direct_job.id])
        self.assertIn("financial_split", rows[self.direct_job.id])
        self.assertIn("assigned_specs", rows[self.shop_only_job.id])
        self.assertNotIn("sourced_shop_identity", rows[self.shop_only_job.id])

    def test_settlement_is_manager_level_only_when_shop_owner_is_broker_of_record(self):
        self.api_client.force_authenticate(user=self.shop_owner)

        direct_response = self.api_client.get(f"/api/managed-jobs/{self.direct_job.id}/settlement/")
        shop_only_response = self.api_client.get(f"/api/managed-jobs/{self.shop_only_job.id}/settlement/")

        self.assertEqual(direct_response.status_code, 200)
        self.assertEqual(shop_only_response.status_code, 200)
        self.assertEqual(direct_response.json()["broker_payout"], "225.00")
        self.assertEqual(direct_response.json()["printy_fee"], "225.00")
        self.assertEqual(shop_only_response.json()["shop_payout"], "1050.00")
        self.assertNotIn("broker_payout", shop_only_response.json())

    def test_broker_of_record_arithmetic_matches_margin_after_printy_fee(self):
        assignment = self.direct_job.assignments.filter(reassigned_from__isnull=True).first()

        self.assertEqual(
            self.direct_job.broker_payout + assignment.shop_payout,
            self.direct_job.client_total - self.direct_job.printy_fee,
        )

    def test_dispatch_allows_broker_of_record_shop_owner_but_not_shop_only_owner(self):
        self.api_client.force_authenticate(user=self.shop_owner)

        direct_response = self.api_client.post(
            f"/api/dashboard/partner/jobs/{self.direct_job.id}/dispatch/",
            {},
            format="json",
        )
        shop_only_response = self.api_client.post(
            f"/api/dashboard/partner/jobs/{self.shop_only_job.id}/dispatch/",
            {},
            format="json",
        )

        self.assertEqual(direct_response.status_code, 201)
        self.assertEqual(shop_only_response.status_code, 403)

    def test_manager_proof_approval_allows_broker_of_record_shop_owner_only(self):
        direct_proof = self._upload_proof(self.direct_job)
        self._upload_proof(self.shop_only_job)
        self.api_client.force_authenticate(user=self.shop_owner)

        direct_response = self.api_client.post(
            f"/api/dashboard/manager/jobs/{self.direct_job.id}/proof-approval/",
            {"action": "approve"},
            format="json",
        )
        shop_only_response = self.api_client.post(
            f"/api/dashboard/manager/jobs/{self.shop_only_job.id}/proof-approval/",
            {"action": "approve"},
            format="json",
        )

        self.assertEqual(direct_response.status_code, 200)
        self.assertEqual(shop_only_response.status_code, 403)
        direct_proof.refresh_from_db()
        self.assertEqual(direct_proof.status, JobFileStatus.MANAGER_APPROVED)

    def test_file_approve_reject_revision_allow_broker_of_record_shop_owner_only(self):
        direct_approve = self._upload_proof(self.direct_job)
        shop_only_approve = self._upload_proof(self.shop_only_job)
        self.api_client.force_authenticate(user=self.shop_owner)

        approve_response = self.api_client.post(f"/api/job-files/{direct_approve.id}/approve/", {}, format="json")
        shop_only_response = self.api_client.post(f"/api/job-files/{shop_only_approve.id}/approve/", {}, format="json")
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(shop_only_response.status_code, 403)
        direct_approve.refresh_from_db()
        self.assertEqual(direct_approve.status, JobFileStatus.MANAGER_APPROVED)

        direct_reject = self._upload_proof(self.direct_job)
        reject_response = self.api_client.post(f"/api/job-files/{direct_reject.id}/reject/", {}, format="json")
        self.assertEqual(reject_response.status_code, 200)
        direct_reject.refresh_from_db()
        self.assertEqual(direct_reject.status, JobFileStatus.MANAGER_REJECTED)

        revision_response = self.api_client.post(f"/api/job-files/{direct_approve.id}/request-revision/", {}, format="json")
        self.assertEqual(revision_response.status_code, 200)
        direct_approve.refresh_from_db()
        self.assertEqual(direct_approve.status, JobFileStatus.REVISION_REQUESTED)
