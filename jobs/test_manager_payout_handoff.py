"""Regression tests for the QuoteFinancialSplit -> ManagedJob -> payout handoff.

These cover the bug found in the 2026-09-29 verification: ManagedJob was created
without copying broker_payout from the authoritative QuoteFinancialSplit, so the
manager payout resolved to 0 and was silently skipped at release time.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob, ManagedJobPayout
from jobs.payout_services import record_payout_failure, release_managed_job_payouts
from payments.models import Payment
from quotes.models import QuoteFinancialSplit
from shops.models import Shop


User = get_user_model()

# Job A, verbatim from the failing verification.
JOB_A = {
    "client_total": Decimal("4226.00"),
    "shop_payout": Decimal("2536.00"),
    "broker_payout": Decimal("815.00"),
    "printy_fee": Decimal("875.00"),
}
# Job B, verbatim from the failing verification.
JOB_B = {
    "client_total": Decimal("4342.00"),
    "shop_payout": Decimal("2605.00"),
    "broker_payout": Decimal("837.00"),
    "printy_fee": Decimal("900.00"),
}


class ManagerPayoutHandoffTestCase(TestCase):
    """Covers requirements A-F plus the reconciliation requirement in Task 3."""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            email="handoff-admin@example.com",
            password="pass12345",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            email="handoff-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="handoff-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
        )
        self.shop_owner = User.objects.create_user(
            email="handoff-shop@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            name="Handoff Shop",
            owner=self.shop_owner,
            is_active=True,
        )
        self.api = APIClient()

    def _make_job(self, split_values, *, populate_broker_payout=True):
        """Build a READY, paid job backed by real split-derived values."""
        managed_job = ManagedJob.objects.create(
            title="Handoff Job",
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            status=ManagedJobStatus.READY,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=split_values["client_total"],
            broker_payout=split_values["broker_payout"] if populate_broker_payout else None,
            printy_fee=split_values["printy_fee"],
            ready_at=timezone.now(),
        )
        assignment = JobAssignment.objects.create(
            managed_job=managed_job,
            assigned_shop=self.shop,
            status="ready",
            shop_payout=split_values["shop_payout"],
        )
        Payment.objects.create(
            managed_job=managed_job,
            payer=self.client_user,
            amount=split_values["client_total"],
            expected_amount=split_values["client_total"],
            received_amount=split_values["client_total"],
            method=Payment.METHOD_MANUAL,
            provider="manual",
            status=Payment.STATUS_PAID,
            confirmed_at=timezone.now(),
        )
        return managed_job, assignment

    def _release(self, user=None):
        self.api.force_authenticate(user=user or self.admin_user)
        return self.api.post(reverse("managed-job-payout-release", kwargs={"pk": self.managed_job.id}), {})

    # --- A. QuoteFinancialSplit -> ManagedJob -------------------------------

    def test_a_split_values_are_copied_onto_managed_job_at_creation(self):
        """TEST 1: the real production path must carry the whole split.

        The job that actually exists in production is built by
        create_managed_job_from_payment, which runs when the M-Pesa callback
        confirms payment. It is NOT the same code path as
        create_managed_job_from_accepted_quote, so it is driven here through
        mark_payment_paid - the exact entry point the callback uses.
        """
        payment = self._create_accepted_quote_with_payment(JOB_A)

        from payments.services import mark_payment_paid

        mark_payment_paid(payment=payment, receipt_number="HANDOFF-1")
        managed_job = ManagedJob.objects.get(source_quote=payment.quote)

        self.assertEqual(managed_job.client_total, JOB_A["client_total"])
        self.assertEqual(managed_job.broker_payout, JOB_A["broker_payout"])
        self.assertEqual(managed_job.printy_fee, JOB_A["printy_fee"])

    def test_a1b_accepted_quote_path_also_carries_the_split(self):
        """The quote-acceptance path must copy the same authoritative values."""
        from jobs.managed_services import create_managed_job_from_accepted_quote

        quote, quote_request = self._create_accepted_quote(JOB_A)
        managed_job = create_managed_job_from_accepted_quote(quote_request=quote_request, quote=quote)

        self.assertEqual(managed_job.client_total, JOB_A["client_total"])
        self.assertEqual(managed_job.broker_payout, JOB_A["broker_payout"])
        self.assertEqual(managed_job.printy_fee, JOB_A["printy_fee"])

    def test_a2_shop_share_reaches_the_assignment_through_the_production_path(self):
        """The shop share is carried by JobAssignment, not ManagedJob.

        ManagedJob has no shop_payout column; the authoritative split's
        shop_payout is written onto the JobAssignment that the same production
        path creates. Assert it arrives as the exact split Decimal.
        """
        from jobs.services.managed_job_creation import ensure_job_assignment_for_paid_job

        payment = self._create_accepted_quote_with_payment(JOB_A)
        from payments.services import mark_payment_paid

        mark_payment_paid(payment=payment, receipt_number="HANDOFF-2")
        managed_job = ManagedJob.objects.get(source_quote=payment.quote)
        assignment = ensure_job_assignment_for_paid_job(managed_job=managed_job)

        self.assertEqual(assignment.assigned_shop, self.shop)
        self.assertEqual(assignment.shop_payout, JOB_A["shop_payout"])
        # And the whole split is present exactly once, summing to the client total.
        self.assertEqual(
            managed_job.broker_payout + assignment.shop_payout + managed_job.printy_fee,
            JOB_A["client_total"],
        )

    def _create_accepted_quote_with_payment(self, split_values, *, with_manager=True):
        """An accepted, split-backed quote plus the Payment that closes it."""
        quote, quote_request = self._create_accepted_quote(split_values, with_manager=with_manager)
        payment = Payment.objects.create(
            quote=quote,
            payer=self.client_user,
            amount=split_values["client_total"],
            expected_amount=split_values["client_total"],
            method=Payment.METHOD_MPESA,
            provider="mpesa",
            status=Payment.STATUS_PENDING,
        )
        return payment

    def test_a3_partner_led_quote_resolves_its_manager_as_the_broker(self):
        """The manager must be on the job even when the request is unassigned.

        In the partner-led flow the manager creates the quote on a request the
        client submitted, so quote_request.assigned_manager is never set. If
        only assigned_manager is consulted the job gets broker=NULL and the
        split's promised manager share becomes unpayable.
        """
        payment = self._create_accepted_quote_with_payment(JOB_A, with_manager=False)
        self.assertIsNone(payment.quote.quote_request.assigned_manager_id)
        # The partner is the one who priced the markup, so they are the manager.
        self.assertEqual(payment.quote.created_by_id, self.manager.id)

        from payments.services import mark_payment_paid

        mark_payment_paid(payment=payment, receipt_number="HANDOFF-3")
        managed_job = ManagedJob.objects.get(source_quote=payment.quote)

        self.assertEqual(managed_job.broker_id, self.manager.id)
        self.assertEqual(managed_job.broker_payout, JOB_A["broker_payout"])

    def test_a4_promised_manager_share_with_no_broker_is_refused(self):
        """Safety net: a promised manager share must never be silently dropped.

        Even if the job somehow carries neither a broker nor the split value,
        releasing only the shop would swallow the manager's money.
        """
        self.managed_job, self.assignment = self._make_job(JOB_A, populate_broker_payout=False)
        self.managed_job.broker = None
        self.managed_job.save(update_fields=["broker", "updated_at"])
        quote, _ = self._create_accepted_quote(JOB_A)
        self.managed_job.source_quote = quote
        self.managed_job.save(update_fields=["source_quote", "updated_at"])

        response = self._release()

        self.assertEqual(response.status_code, 400)
        self.assertIn("no broker", response.json()["detail"])
        self.assertEqual(ManagedJobPayout.objects.count(), 0)

    def _create_accepted_quote(self, split_values, *, with_manager=True):
        """Create a minimal accepted quote carrying a real financial split."""
        from pricing.models import PlatformFeePolicy
        from quotes.models import Quote, QuoteRequest

        PlatformFeePolicy.objects.update(is_active=False)
        policy = PlatformFeePolicy.objects.create(
            name="Handoff policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.10"),
            add_platform_fee_on_top=False,
        )
        customer = self.manager
        quote_request = QuoteRequest.objects.create(
            shop=None,
            created_by=self.client_user,
            assigned_manager=self.manager if with_manager else None,
            customer_name="End Client",
            customer_email=self.client_user.email,
            status="submitted",
            request_snapshot={"source": "manager_led_intake"},
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=customer,
            total=split_values["client_total"],
            status="accepted",
            accepted_at=timezone.now(),
        )
        QuoteFinancialSplit.objects.create(
            quote=quote,
            policy_used=policy,
            production_cost=Decimal("2117.00"),
            broker_client_price=split_values["client_total"],
            gross_margin=Decimal("2109.00"),
            printer_side_fee=Decimal("200.00"),
            broker_margin_fee=Decimal("173.00"),
            printy_fee=split_values["printy_fee"],
            shop_payout=split_values["shop_payout"],
            broker_payout=split_values["broker_payout"],
            client_total=split_values["client_total"],
            max_allowed_client_price=Decimal("9000.00"),
            applied_markup_multiple=Decimal("1.7500"),
        )
        return quote, quote_request

    # --- B/C/D. Payout creation on release ----------------------------------

    def test_b_job_a_release_creates_manager_payout_of_815(self):
        self.managed_job, self.assignment = self._make_job(JOB_A)

        response = self._release()

        self.assertEqual(response.status_code, 200)
        manager_payout = ManagedJobPayout.objects.get(
            managed_job=self.managed_job,
            recipient_role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
        )
        self.assertEqual(manager_payout.amount, Decimal("815.00"))
        self.assertEqual(manager_payout.recipient, self.manager)
        self.assertEqual(manager_payout.status, ManagedJobPayout.STATUS_RELEASED)
        # Printy has no outbound provider, so the record must say so explicitly.
        self.assertEqual(manager_payout.disbursement_mode, ManagedJobPayout.DISBURSEMENT_MANUAL)
        self.assertFalse(ManagedJobPayout.AUTOMATIC_DISBURSEMENT_AVAILABLE)

    def test_c_shop_payout_stays_correct_at_2536(self):
        self.managed_job, self.assignment = self._make_job(JOB_A)

        self._release()

        shop_payout = ManagedJobPayout.objects.get(
            managed_job=self.managed_job,
            recipient_role=ManagedJobPayout.RECIPIENT_ROLE_SHOP,
        )
        self.assertEqual(shop_payout.amount, Decimal("2536.00"))
        self.assertEqual(shop_payout.recipient, self.shop_owner)

    def test_d_job_b_manager_and_shop_payouts(self):
        self.managed_job, self.assignment = self._make_job(JOB_B)

        self._release()

        payouts = {
            p.recipient_role: p.amount
            for p in ManagedJobPayout.objects.filter(managed_job=self.managed_job)
        }
        self.assertEqual(payouts[ManagedJobPayout.RECIPIENT_ROLE_MANAGER], Decimal("837.00"))
        self.assertEqual(payouts[ManagedJobPayout.RECIPIENT_ROLE_SHOP], Decimal("2605.00"))

    def test_reconciliation_balances_for_both_jobs(self):
        """manager payout + shop payout + Printy fee == client total, per job."""
        for values in (JOB_A, JOB_B):
            with self.subTest(client_total=values["client_total"]):
                self.managed_job, self.assignment = self._make_job(values)

                response = self._release()

                self.assertEqual(response.status_code, 200)
                # The release service reports the four-way reconciliation.
                reconciliation = response.json()["reconciliation"]
                self.assertEqual(reconciliation["manager_payout"], str(values["broker_payout"]))
                self.assertEqual(reconciliation["shop_payout"], str(values["shop_payout"]))
                self.assertEqual(reconciliation["printy_fee"], str(values["printy_fee"]))
                self.assertEqual(reconciliation["client_total"], str(values["client_total"]))
                self.assertEqual(reconciliation["balances"], "0.00")
                self.assertTrue(reconciliation["reconciled"])
                self._tearDownJob()
        # Explicit per-job reconciliation (Task 3):
        self.assertEqual(Decimal("815.00") + Decimal("2536.00") + Decimal("875.00"), Decimal("4226.00"))
        self.assertEqual(Decimal("837.00") + Decimal("2605.00") + Decimal("900.00"), Decimal("4342.00"))

    def _tearDownJob(self):
        ManagedJobPayout.objects.all().delete()
        Payment.objects.all().delete()
        JobAssignment.objects.all().delete()
        ManagedJob.objects.all().delete()

    # --- E. Idempotency ------------------------------------------------------

    def test_e_repeated_release_creates_no_duplicate_payouts(self):
        self.managed_job, self.assignment = self._make_job(JOB_A)

        first = self._release()
        second = self._release()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["idempotent"])
        # Still exactly one manager + one shop row, no duplicate transfers.
        self.assertEqual(ManagedJobPayout.objects.filter(managed_job=self.managed_job).count(), 2)
        manager_rows = ManagedJobPayout.objects.filter(
            managed_job=self.managed_job,
            recipient_role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
        )
        self.assertEqual(manager_rows.count(), 1)

    # --- F. Authorization ----------------------------------------------------

    def test_f_non_admin_release_is_forbidden(self):
        self.managed_job, self.assignment = self._make_job(JOB_A)

        response = self._release(user=self.manager)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ManagedJobPayout.objects.count(), 0)

    def test_f_shop_and_client_cannot_release(self):
        self.managed_job, self.assignment = self._make_job(JOB_A)
        for user in (self.shop_owner, self.client_user):
            with self.subTest(user=user.email):
                self.assertEqual(self._release(user=user).status_code, 403)
        self.assertEqual(ManagedJobPayout.objects.count(), 0)

    # --- Silent-skip guard ---------------------------------------------------

    def test_missing_broker_payout_is_refused_not_silently_skipped(self):
        """If the job lost the manager value, release must fail loudly, not pay only the shop."""
        self.managed_job, self.assignment = self._make_job(JOB_A, populate_broker_payout=False)
        # Attach an authoritative split that promises the manager money.
        quote, _ = self._create_accepted_quote(JOB_A)
        self.managed_job.source_quote = quote
        self.managed_job.save(update_fields=["source_quote", "updated_at"])

        response = self._release()

        self.assertEqual(response.status_code, 400)
        self.assertIn("owed", response.json()["detail"])
        # Crucially, the shop must NOT have been paid in isolation.
        self.assertEqual(ManagedJobPayout.objects.count(), 0)


class PayoutFailureHandlingTestCase(TestCase):
    """Task 6 / G: a failed payout must be preserved, not marked paid, and retryable."""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            email="fail-admin@example.com",
            password="pass12345",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            email="fail-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="fail-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
        )
        self.shop_owner = User.objects.create_user(
            email="fail-shop@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(name="Fail Shop", owner=self.shop_owner, is_active=True)
        self.managed_job = ManagedJob.objects.create(
            title="Fail Job",
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            status=ManagedJobStatus.READY,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=JOB_A["client_total"],
            broker_payout=JOB_A["broker_payout"],
            printy_fee=JOB_A["printy_fee"],
            ready_at=timezone.now(),
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.managed_job,
            assigned_shop=self.shop,
            status="ready",
            shop_payout=JOB_A["shop_payout"],
        )
        Payment.objects.create(
            managed_job=self.managed_job,
            payer=self.client_user,
            amount=JOB_A["client_total"],
            expected_amount=JOB_A["client_total"],
            received_amount=JOB_A["client_total"],
            method=Payment.METHOD_MANUAL,
            provider="manual",
            status=Payment.STATUS_PAID,
            confirmed_at=timezone.now(),
        )

    def test_g_failure_is_recorded_and_never_marked_paid(self):
        payout = record_payout_failure(
            managed_job=self.managed_job,
            role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
            reason="Provider rejected beneficiary phone number",
            recipient=self.manager,
            amount=JOB_A["broker_payout"],
            provider_response={"result_code": "4001", "error": "invalid phone"},
        )

        self.assertEqual(payout.status, ManagedJobPayout.STATUS_FAILED)
        self.assertNotEqual(payout.status, ManagedJobPayout.STATUS_RELEASED)
        self.assertIsNone(payout.released_at)
        self.assertEqual(payout.failure_reason, "Provider rejected beneficiary phone number")
        self.assertEqual(payout.provider_response["result_code"], "4001")

    def test_failure_is_retryable_without_creating_duplicate(self):
        record_payout_failure(
            managed_job=self.managed_job,
            role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
            reason="transient outage",
            recipient=self.manager,
            amount=JOB_A["broker_payout"],
        )
        # Retry succeeds via the real release path.
        result = release_managed_job_payouts(managed_job=self.managed_job, released_by=self.admin_user)

        manager_payout = ManagedJobPayout.objects.get(
            managed_job=self.managed_job,
            recipient_role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
        )
        self.assertEqual(manager_payout.status, ManagedJobPayout.STATUS_RELEASED)
        self.assertEqual(manager_payout.amount, Decimal("815.00"))
        # The failure trail was cleared and the retry recorded, still one row.
        self.assertEqual(manager_payout.failure_reason, "")
        self.assertIn("retried_at", manager_payout.metadata)
        self.assertEqual(
            ManagedJobPayout.objects.filter(
                managed_job=self.managed_job,
                recipient_role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
            ).count(),
            1,
        )
        self.assertTrue(result["reconciliation"]["reconciled"])

    def test_failed_payout_is_visible_to_admin_via_settlement_api(self):
        record_payout_failure(
            managed_job=self.managed_job,
            role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
            reason="needs retry",
            recipient=self.manager,
            amount=JOB_A["broker_payout"],
        )
        api = APIClient()
        api.force_authenticate(user=self.admin_user)
        response = api.get(reverse("managed-job-settlement", kwargs={"pk": self.managed_job.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["payout_failures"]), 1)
        self.assertTrue(payload["requires_manual_transfer"])
        self.assertFalse(payload["automatic_disbursement_available"])
