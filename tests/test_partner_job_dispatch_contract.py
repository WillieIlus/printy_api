"""Contract tests for the manager (partner) job dispatch endpoint.

The manager workbench decides whether a dispatch succeeded by reading
``result.dispatched`` and then mirroring ``dispatched_at``,
``assignment_status`` and ``shop_name`` back into the job row it is
displaying. If the endpoint omits ``dispatched`` the row is never updated,
the job stays in ``dispatchableJobs`` and the manager is shown a dispatch
button for a job that has already been dispatched.
"""

from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobAssignmentStatus,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)
from jobs.artwork_confirmation import (
    request_client_artwork_confirmation,
    respond_to_client_artwork_confirmation,
)
from jobs.models import JobAssignment, ManagedJob
from notifications.models import Notification
from payments.services import handle_stk_callback, initiate_stk_push
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.acceptance import accept_quote_for_payment
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from shops.models import Shop


User = get_user_model()


class PartnerJobDispatchContractTestCase(TestCase):
    """Drives the real payment -> dispatch path, then asserts the HTTP body."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            email="dispatch-contract-client@example.com",
            password="pass",
            role=User.Role.CLIENT,
        )
        self.broker = User.objects.create_user(
            email="dispatch-contract-broker@example.com",
            password="pass",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        # An unrelated broker who must never be able to touch this job.
        self.other_broker = User.objects.create_user(
            email="dispatch-contract-other-broker@example.com",
            password="pass",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.shop_owner = User.objects.create_user(
            email="dispatch-contract-shop@example.com",
            password="pass",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            name="Dispatch Contract Print Shop",
            owner=self.shop_owner,
            is_active=True,
            opening_time=time(8, 0),
            closing_time=time(18, 0),
        )
        self.policy = PlatformFeePolicy.objects.create(
            name="Dispatch contract policy",
            is_active=True,
            printer_fee_rate=Decimal("0.0500"),
            broker_margin_fee_rate=Decimal("0.1500"),
            add_platform_fee_on_top=False,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            assigned_manager=self.broker,
            customer_name="Dispatch Contract Client",
            customer_email="dispatch-contract-client@example.com",
            customer_phone="+254700000000",
            status=QuoteStatus.SUBMITTED,
        )
        self.production_option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.broker,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=self.production_option,
            created_by=self.broker,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1500.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        create_quote_financial_split(
            quote=self.quote,
            production_option=self.production_option,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.policy,
        )

    def _paid_managed_job(self) -> ManagedJob:
        create_quote_financial_split(
            quote=self.quote,
            production_option=self.production_option,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.policy,
        )
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        handle_stk_callback(
            callback_payload={
                "Body": {
                    "stkCallback": {
                        "CheckoutRequestID": stk.checkout_request_id,
                        "MerchantRequestID": stk.merchant_request_id,
                        "ResultCode": 0,
                        "ResultDesc": "Success",
                        "CallbackMetadata": {
                            "Item": [
                                {"Name": "Amount", "Value": "1500.00"},
                                {"Name": "MpesaReceiptNumber", "Value": "DISPATCHRCPT"},
                            ]
                        },
                    }
                }
            }
        )
        return ManagedJob.objects.get(source_quote=self.quote)

    def _dispatch(self, managed_job: ManagedJob, user=None):
        self.client.force_login(user or self.broker)
        return self.client.post(
            reverse("dashboard-partner-job-dispatch", kwargs={"pk": managed_job.pk}),
            {},
            content_type="application/json",
        )

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_dispatch_response_carries_the_fields_the_manager_ui_reads(self):
        managed_job = self._paid_managed_job()

        response = self._dispatch(managed_job)

        self.assertEqual(response.status_code, 201)
        body = response.json()

        # manager.ts reads `dispatched` to decide whether to update the row.
        self.assertIs(body.get("dispatched"), True)
        self.assertEqual(body.get("job_id"), managed_job.pk)
        self.assertIsNotNone(body.get("assignment_id"))
        self.assertIsNotNone(body.get("dispatched_at"))
        # manager.ts mirrors these onto the row it is rendering.
        self.assertEqual(body.get("assignment_status"), ManagedJobAssignmentStatus.ASSIGNED)
        self.assertEqual(body.get("shop_name"), self.shop.name)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_dispatch_response_keeps_the_existing_shop_and_payout_fields(self):
        managed_job = self._paid_managed_job()

        body = self._dispatch(managed_job).json()

        self.assertEqual(body.get("managed_job_id"), managed_job.pk)
        self.assertEqual(body.get("shop_id"), self.shop.pk)
        self.assertIsNotNone(body.get("shop_payout"))

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_repeat_dispatch_still_reports_dispatched_so_the_ui_converges(self):
        managed_job = self._paid_managed_job()

        first = self._dispatch(managed_job)
        second = self._dispatch(managed_job)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertIs(second.json().get("dispatched"), True)
        self.assertEqual(
            second.json().get("assignment_id"),
            first.json().get("assignment_id"),
        )
        self.assertEqual(JobAssignment.objects.count(), 1)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_dispatch_response_reflects_the_persisted_job_state(self):
        managed_job = self._paid_managed_job()

        body = self._dispatch(managed_job).json()
        managed_job.refresh_from_db()

        self.assertEqual(managed_job.status, ManagedJobStatus.ASSIGNED)
        self.assertEqual(managed_job.assignment_status, ManagedJobAssignmentStatus.ASSIGNED)
        self.assertIsNotNone(managed_job.dispatched_at)
        self.assertEqual(managed_job.assigned_shop_id, self.shop.pk)
        # The reported timestamp must be the one that was actually stored.
        self.assertEqual(
            body["dispatched_at"][:19],
            managed_job.dispatched_at.isoformat()[:19],
        )

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_dispatched_job_reaches_the_shop_owners_production_queue(self):
        managed_job = self._paid_managed_job()
        self.assertEqual(JobAssignment.objects.count(), 0)

        dispatched = self._dispatch(managed_job)

        self.assertEqual(dispatched.status_code, 201)
        # The assignment is the printer's work order.
        assignment = JobAssignment.objects.get(pk=dispatched.json()["assignment_id"])
        self.assertEqual(assignment.assigned_shop_id, self.shop.pk)
        self.assertEqual(assignment.status, JobAssignmentStatus.PENDING)

        # ...and the job surfaces in the queue the shop owner actually reads.
        self.client.force_login(self.shop_owner)
        queue = self.client.get(reverse("dashboard-production-jobs"))

        self.assertEqual(queue.status_code, 200)
        rows = queue.json()["results"]
        self.assertIn(
            managed_job.pk,
            [row["id"] for row in rows],
        )

    # --- Gates and authorization -------------------------------------------
    # Ported from `api/tests.py::PartnerDispatchValidationTestCase`, which was
    # permanently skipped and so never guarded any of this.

    def test_unpaid_job_cannot_be_dispatched(self):
        managed_job = ManagedJob.objects.create(
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            broker=self.broker,
            assigned_shop=self.shop,
            payment_status=ManagedJobPaymentStatus.PENDING,
            status=ManagedJobStatus.AWAITING_PAYMENT,
        )

        response = self._dispatch(managed_job)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Client payment must be confirmed before dispatch.",
        )
        self.assertEqual(JobAssignment.objects.count(), 0)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_job_with_pending_artwork_confirmation_cannot_be_dispatched(self):
        managed_job = self._paid_managed_job()
        request_client_artwork_confirmation(managed_job=managed_job, actor=self.broker)

        response = self._dispatch(managed_job)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Client artwork confirmation is required before dispatch.",
        )
        self.assertEqual(JobAssignment.objects.count(), 0)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_job_with_rejected_artwork_cannot_be_dispatched(self):
        managed_job = self._paid_managed_job()
        request_client_artwork_confirmation(managed_job=managed_job, actor=self.broker)
        respond_to_client_artwork_confirmation(
            managed_job=managed_job,
            actor=self.client_user,
            approved=False,
        )

        response = self._dispatch(managed_job)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Client requested artwork changes before dispatch.",
        )
        self.assertEqual(JobAssignment.objects.count(), 0)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_client_cannot_dispatch_the_job(self):
        managed_job = self._paid_managed_job()

        response = self._dispatch(managed_job, user=self.client_user)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(JobAssignment.objects.count(), 0)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_shop_owner_cannot_dispatch_the_job(self):
        managed_job = self._paid_managed_job()

        response = self._dispatch(managed_job, user=self.shop_owner)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(JobAssignment.objects.count(), 0)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_unrelated_broker_cannot_dispatch_someone_elses_job(self):
        """Cross-tenant guard: being a partner is not enough to dispatch a job.

        Regression test for an authorization hole where the role check alone
        short-circuited the ownership check, letting any partner dispatch any
        other partner's job.
        """
        managed_job = self._paid_managed_job()

        response = self._dispatch(managed_job, user=self.other_broker)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(JobAssignment.objects.count(), 0)
        managed_job.refresh_from_db()
        self.assertIsNone(managed_job.dispatched_at)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_unrelated_broker_cannot_see_the_job_in_the_partner_list(self):
        """The dispatch guard and the list scope must agree."""
        managed_job = self._paid_managed_job()

        self.client.force_login(self.other_broker)
        listing = self.client.get(reverse("dashboard-partner-jobs"))

        self.assertEqual(listing.status_code, 200)
        self.assertNotIn(
            managed_job.pk,
            [row["id"] for row in listing.json()["results"]],
        )

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_shop_owner_who_is_also_the_broker_of_record_may_dispatch(self):
        """A user can be both shop owner and broker; that must still work.

        `get_actor_role` resolves such a user to SHOP, not BROKER, because
        production outranks partner in the role priority. The dispatch guard must
        not exclude them.
        """
        managed_job = self._paid_managed_job()
        ManagedJob.objects.filter(pk=managed_job.pk).update(broker=self.shop_owner)
        managed_job.refresh_from_db()

        response = self._dispatch(managed_job, user=self.shop_owner)

        self.assertEqual(response.status_code, 201)
        self.assertIs(response.json().get("dispatched"), True)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_client_who_created_the_quote_request_may_not_dispatch(self):
        """Scope must not be satisfied by merely authoring the request."""
        managed_job = self._paid_managed_job()
        # The client is source_quote_request.created_by, so a scope-only check
        # would wrongly let them dispatch.
        self.assertEqual(managed_job.source_quote_request.created_by_id, self.client_user.pk)

        response = self._dispatch(managed_job, user=self.client_user)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(JobAssignment.objects.count(), 0)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_dispatch_notifies_the_shop_owner(self):
        managed_job = self._paid_managed_job()

        self._dispatch(managed_job)

        self.assertTrue(
            Notification.objects.filter(
                user=self.shop_owner,
                notification_type=Notification.JOB_STATUS_UPDATED,
                object_type="managed_job",
                object_id=managed_job.pk,
            ).exists()
        )

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_assignment_payout_is_the_shop_payout_not_the_client_total(self):
        managed_job = self._paid_managed_job()

        body = self._dispatch(managed_job).json()
        assignment = JobAssignment.objects.get(pk=body["assignment_id"])
        managed_job.refresh_from_db()

        # The printer is paid the production share. It must never be handed the
        # broker's client-facing price.
        self.assertEqual(assignment.shop_payout, self.quote.financial_split.shop_payout)
        self.assertNotEqual(assignment.shop_payout, managed_job.client_total)
