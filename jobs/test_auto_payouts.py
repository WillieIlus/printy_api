"""Prompt 7 PART 2: automatic payouts via Daraja B2C (disabled by default).

Daraja is never contacted: every test passes a fake B2C client or posts a
fake callback. Automatic payouts are switched on only inside tests, with the
visibly fake config.test_settings.TEST_B2C_SETTINGS.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.admin.models import LogEntry
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from config.payout_config import auto_payout_config_errors
from config.test_settings import TEST_B2C_SETTINGS
from jobs import auto_payouts
from jobs.choices import JobAssignmentStatus, ManagedJobExceptionStatus, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob, ManagedJobPayout
from jobs.payout_services import release_managed_job_payouts
from payments.b2c import B2CAmbiguous, B2CNotSent
from payments.models import Payment
from payments.testing import DARAJA_TEST_PEER_IP, daraja_callback_path
from pricing.models import PlatformFeePolicy
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteFinancialSplit, QuoteRequest
from shops.models import Shop

P = ManagedJobPayout
ON = override_settings(**TEST_B2C_SETTINGS)

CLIENT_TOTAL = Decimal("1200.00")
SHOP_PAYOUT = Decimal("800.00")
MANAGER_PAYOUT = Decimal("240.00")
PRINTY_FEE = Decimal("160.00")


class FakeB2C:
    """Records requests; ``behaviour`` is "accept", or an exception to raise."""

    def __init__(self, behaviour="accept"):
        self.behaviour = behaviour
        self.calls = []

    def send(self, *, msisdn, amount, originator_conversation_id, remarks, occasion=""):
        self.calls.append(
            {"msisdn": msisdn, "amount": amount, "key": originator_conversation_id, "occasion": occasion}
        )
        behaviour = self.behaviour(len(self.calls)) if callable(self.behaviour) else self.behaviour
        if isinstance(behaviour, Exception):
            raise behaviour
        return {
            "ConversationID": f"AG_CONV_{originator_conversation_id}",
            "OriginatorConversationID": originator_conversation_id,
            "ResponseCode": "0",
        }


def result_body(payout, *, code=0, amount=None, receipt="UBX123ABC", conversation_id=None, occ=None):
    params = [
        {"Key": "TransactionAmount", "Value": float(amount if amount is not None else payout.amount)},
        {"Key": "TransactionReceipt", "Value": receipt},
        {"Key": "ReceiverPartyPublicName", "Value": "254700000000 - Test"},
    ]
    return {
        "Result": {
            "ResultType": 0,
            "ResultCode": code,
            "ResultDesc": "The service request is processed successfully." if code == 0 else "Declined",
            "OriginatorConversationID": occ if occ is not None else payout.idempotency_key,
            "ConversationID": conversation_id if conversation_id is not None else payout.b2c_conversation_id,
            "TransactionID": receipt,
            "ResultParameters": {"ResultParameter": params} if code == 0 else {},
        }
    }


class AutoPayoutBase(TestCase):
    def setUp(self):
        PlatformFeePolicy.objects.update(is_active=False)
        self.policy = PlatformFeePolicy.objects.create(name="Auto payout policy", is_active=True)
        self.admin = User.objects.create_superuser(email="ap-admin@example.com", password="x")
        self.client_user = User.objects.create_user(email="ap-client@example.com", password="x", role=User.Role.CLIENT)
        self.manager = User.objects.create_user(
            email="ap-manager@example.com", password="x", role=User.Role.PARTNER, partner_profile_enabled=True
        )
        self.printer = User.objects.create_user(email="ap-printer@example.com", password="x", role=User.Role.PRODUCTION)
        self.verify_phone(self.manager, "254711000001")
        self.verify_phone(self.printer, "254711000002")
        self.shop = Shop.objects.create(owner=self.printer, name="AP Press", slug="ap-press", is_active=True)
        self.job = self.make_job()

    @staticmethod
    def verify_phone(user, number):
        UserProfile.objects.update_or_create(
            user=user, defaults={"phone_normalized": number, "phone_verified_at": timezone.now()}
        )

    def make_job(self, *, shop_payout=SHOP_PAYOUT, manager_payout=MANAGER_PAYOUT, client_total=CLIENT_TOTAL,
                 printy_fee=PRINTY_FEE, split=True, completion_method=ManagedJob.COMPLETION_BUYER_HANDOVER,
                 status=ManagedJobStatus.COMPLETED, paid=True, tag=None):
        tag = tag or f"{ManagedJob.objects.count() + 1}"
        request = QuoteRequest.objects.create(
            shop=self.shop, created_by=self.client_user, customer_name="AP", status=QuoteStatus.CLOSED,
            request_snapshot={},
        )
        quote = Quote.objects.create(
            quote_request=request, shop=self.shop, created_by=self.manager, status=QuoteOfferStatus.ACCEPTED,
            total=client_total,
        )
        if split:
            QuoteFinancialSplit.objects.create(
                quote=quote,
                policy_used=self.policy,
                production_cost=shop_payout,
                broker_client_price=client_total,
                gross_margin=client_total - shop_payout,
                printer_side_fee=Decimal("0.00"),
                broker_margin_fee=printy_fee,
                printy_fee=printy_fee,
                shop_payout=shop_payout,
                manager_payout=manager_payout,
                broker_payout=manager_payout,
                client_total=client_total,
                max_allowed_client_price=client_total * 2,
                applied_markup_multiple=Decimal("1.5000"),
            )
        job = ManagedJob.objects.create(
            managed_reference=f"MJ-AP-{tag}",
            title="Auto payout job",
            source_quote_request=request,
            source_quote=quote,
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.manager,
            status=status,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=client_total,
            printy_fee=printy_fee,
            broker_payout=manager_payout,
            delivered_at=timezone.now(),
            completed_at=timezone.now() if status == ManagedJobStatus.COMPLETED else None,
            completion_method=completion_method if status == ManagedJobStatus.COMPLETED else "",
        )
        JobAssignment.objects.create(
            managed_job=job, assigned_shop=self.shop, source_quote=quote, shop_payout=shop_payout,
            status=JobAssignmentStatus.COMPLETED,
        )
        if paid:
            Payment.objects.create(
                quote=quote, managed_job=job, payer=self.client_user, amount=client_total,
                status=Payment.STATUS_PAID, mpesa_receipt_number=f"PAID{tag}",
            )
        return job

    def payouts(self, job=None):
        return {p.recipient_role: p for p in P.objects.filter(managed_job=job or self.job)}

    def run_payouts(self, fake=None, **kwargs):
        fake = fake or FakeB2C()
        summary = auto_payouts.run_auto_payouts(client=fake, **kwargs)
        return fake, summary

    def post_callback(self, body, kind="result", signed=True, ip=DARAJA_TEST_PEER_IP):
        path = f"/api/payments/mpesa/b2c/{kind}/"
        if signed:
            path = daraja_callback_path(path)
        return APIClient().post(path, body, format="json", REMOTE_ADDR=ip)

    def as_admin(self, user=None):
        api = APIClient()
        api.force_authenticate(user or self.admin)
        return api


class DisabledByDefaultTests(AutoPayoutBase):
    def test_disabled_sends_nothing_and_creates_nothing(self):
        fake, summary = self.run_payouts()
        self.assertFalse(summary["enabled"])
        self.assertEqual(fake.calls, [])
        self.assertFalse(P.objects.exists())

    def test_command_reports_disabled(self):
        out = StringIO()
        call_command("run_auto_payouts", stdout=out)
        self.assertIn("disabled", out.getvalue())
        self.assertFalse(P.objects.exists())

    @override_settings(AUTO_PAYOUTS_ENABLED=True)
    def test_enabled_without_b2c_settings_fails_closed(self):
        fake, summary = self.run_payouts()
        self.assertTrue(summary["config_errors"])
        self.assertEqual(fake.calls, [])
        self.assertFalse(P.objects.exists())

    def test_automatic_disbursement_not_available_by_default(self):
        self.assertFalse(auto_payouts.automatic_disbursement_available())


@ON
class HappyPathTests(AutoPayoutBase):
    def test_eligible_job_sends_each_party_once_from_the_stored_split(self):
        fake, summary = self.run_payouts()
        self.assertEqual(summary["sent"], 2)
        sent = {call["occasion"]: call for call in fake.calls}
        self.assertEqual(sent["shop"]["amount"], 800)
        self.assertEqual(sent["shop"]["msisdn"], "254711000002")
        self.assertEqual(sent["manager"]["amount"], 240)
        self.assertEqual(sent["manager"]["msisdn"], "254711000001")
        self.assertEqual(len({call["key"] for call in fake.calls}), 2)
        rows = self.payouts()
        for row in rows.values():
            self.assertEqual(row.status, P.STATUS_PROCESSING)
            self.assertEqual(row.disbursement_mode, P.DISBURSEMENT_AUTOMATIC)
            self.assertTrue(row.b2c_conversation_id.startswith("AG_CONV_"))
            self.assertEqual(row.attempt_count, 1)
        self.assertLessEqual(sum(Decimal(c["amount"]) for c in fake.calls) + PRINTY_FEE, CLIENT_TOTAL)

    def test_success_callbacks_release_and_close_the_job(self):
        self.run_payouts()
        rows = self.payouts()
        for index, row in enumerate(rows.values()):
            response = self.post_callback(result_body(row, receipt=f"UBX00{index}"))
            self.assertEqual(response.status_code, 200)
        rows = self.payouts()
        for row in rows.values():
            self.assertEqual(row.status, P.STATUS_RELEASED)
            self.assertTrue(row.transfer_reference.startswith("UBX00"))
            self.assertIsNotNone(row.released_at)
            self.assertIn("processing->released", [entry["event"] for entry in row.audit_log])
        self.job.refresh_from_db()
        self.assertEqual(self.job.payment_status, ManagedJobPaymentStatus.RELEASED)
        from jobs.models import JobStatusEvent

        self.assertTrue(JobStatusEvent.objects.filter(managed_job=self.job, event_type="payout_released_auto").exists())

    def test_double_run_and_double_callback_pay_once(self):
        fake = FakeB2C()
        self.run_payouts(fake)
        self.run_payouts(fake)
        self.assertEqual(len(fake.calls), 2)
        for row in self.payouts().values():
            body = result_body(row)
            self.post_callback(body)
            self.post_callback(body)  # duplicate
        self.run_payouts(fake)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(P.objects.filter(status=P.STATUS_RELEASED).count(), 2)
        self.assertEqual(P.objects.count(), 2)

    def test_duplicate_callback_is_a_no_op(self):
        self.run_payouts()
        row = self.payouts()["shop"]
        body = result_body(row)
        self.assertEqual(auto_payouts.process_b2c_callback(body), "released")
        row.refresh_from_db()
        log_length = len(row.audit_log)
        self.assertEqual(auto_payouts.process_b2c_callback(body), "duplicate")
        row.refresh_from_db()
        self.assertEqual(len(row.audit_log), log_length)

    def test_rush_premium_hold_does_not_block(self):
        self.job.rush_premium = Decimal("100.00")
        self.job.rush_premium_status = "held"
        self.job.save()
        fake, _ = self.run_payouts()
        self.assertEqual(len(fake.calls), 2)


@ON
class BlockedCasesPayNothingTests(AutoPayoutBase):
    def _only_job(self, job):
        ManagedJob.objects.exclude(pk=job.pk).update(status=ManagedJobStatus.CANCELLED)

    def test_each_blocked_case_pays_nothing(self):
        cases = {
            "delivered_not_completed": (dict(status=ManagedJobStatus.DELIVERED), None, None),
            "payment_pending": (dict(paid=False), None, None),
            "legacy_no_code": (dict(completion_method=ManagedJob.COMPLETION_BUYER_NO_CODE), P.STATUS_NEEDS_REVIEW,
                               auto_payouts.REVIEW_LEGACY_NO_CODE),
            "no_split": (dict(split=False), P.STATUS_NEEDS_REVIEW, auto_payouts.REVIEW_NO_SPLIT),
            "split_exceeds_total": (dict(shop_payout=Decimal("1000.00"), manager_payout=Decimal("300.00")),
                                    P.STATUS_NEEDS_REVIEW, auto_payouts.REVIEW_SPLIT_EXCEEDS_TOTAL),
        }
        for name, (kwargs, expected_status, expected_reason) in cases.items():
            with self.subTest(name):
                job = self.make_job(tag=name, **kwargs)
                self._only_job(job)
                fake, _ = self.run_payouts()
                self.assertEqual(fake.calls, [], name)
                self.assertFalse(
                    P.objects.filter(managed_job=job, status__in=[P.STATUS_PROCESSING, P.STATUS_RELEASED]).exists()
                )
                if expected_status:
                    for row in P.objects.filter(managed_job=job):
                        self.assertEqual(row.status, expected_status)
                        self.assertEqual(row.waiting_reason, expected_reason)

    def test_completed_without_a_recorded_completion_method_is_never_auto_paid(self):
        # Jobs completed before the handover release (migration 0008) have no
        # completion_method; some were completed by the shop without delivery.
        job = self.make_job(tag="pre-handover", completion_method="")
        self._only_job(job)
        fake, _ = self.run_payouts()
        self.assertEqual(fake.calls, [])
        rows = P.objects.filter(managed_job=job)
        self.assertEqual(rows.count(), 2)
        for row in rows:
            self.assertEqual(row.status, P.STATUS_NEEDS_REVIEW)
            self.assertEqual(row.waiting_reason, auto_payouts.REVIEW_COMPLETION_UNVERIFIED)
        shop_row = rows.get(recipient_role=P.RECIPIENT_ROLE_SHOP)
        response = self.as_admin().post(
            f"/api/admin/payouts/{shop_row.pk}/approve/",
            {"confirm_amount": "800", "reason": "Buyer confirmed delivery by phone."}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        fake, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake.calls], ["shop"])

    def test_admin_dispute_completion_is_auto_paid(self):
        job = self.make_job(tag="dispute-done", completion_method=ManagedJob.COMPLETION_ADMIN_DISPUTE)
        self._only_job(job)
        fake, _ = self.run_payouts()
        self.assertEqual(len(fake.calls), 2)

    def test_job_flags_block_with_admin_visible_reason(self):
        cases = {
            "dispute": ({"dispute_open": True, "exception_status": ManagedJobExceptionStatus.DISPUTE_OPEN},
                        auto_payouts.WAIT_DISPUTE_OPEN),
            "lateness_hold": ({"payout_hold": True}, auto_payouts.WAIT_LATENESS_HOLD),
        }
        for name, (fields, reason) in cases.items():
            with self.subTest(name):
                job = self.make_job(tag=name)
                ManagedJob.objects.filter(pk=job.pk).update(**fields)
                self._only_job(job)
                fake, _ = self.run_payouts()
                self.assertEqual(fake.calls, [])
                rows = P.objects.filter(managed_job=job)
                self.assertEqual(rows.count(), 2)
                for row in rows:
                    self.assertEqual(row.status, P.STATUS_PENDING)
                    self.assertEqual(row.waiting_reason, reason)

    def test_payment_needs_review_blocks_even_with_a_paid_row(self):
        Payment.objects.create(
            quote=self.job.source_quote, managed_job=self.job, payer=self.client_user, amount=CLIENT_TOTAL,
            status=Payment.STATUS_NEEDS_REVIEW,
        )
        fake, _ = self.run_payouts()
        self.assertEqual(fake.calls, [])
        self.assertEqual({r.waiting_reason for r in self.payouts().values()}, {auto_payouts.WAIT_PAYMENT_NOT_VERIFIED})

    def test_amount_mismatch_between_job_and_split(self):
        ManagedJob.objects.filter(pk=self.job.pk).update(broker_payout=Decimal("300.00"))
        fake, _ = self.run_payouts()
        self.assertEqual(fake.calls, [])
        self.assertEqual({r.waiting_reason for r in self.payouts().values()}, {auto_payouts.REVIEW_AMOUNT_MISMATCH})

    def test_job_no_longer_completed_blocks_at_send_time(self):
        auto_payouts.prepare_job_payouts(self.job.pk)
        ManagedJob.objects.filter(pk=self.job.pk).update(status=ManagedJobStatus.DELIVERED)
        fake = FakeB2C()
        for row in self.payouts().values():
            self.assertEqual(auto_payouts.send_payout(row.pk, client=fake), "waiting")
            row.refresh_from_db()
            self.assertEqual(row.waiting_reason, auto_payouts.WAIT_JOB_NOT_COMPLETED)
        self.assertEqual(fake.calls, [])

    def test_dispute_opened_after_prepare_blocks_at_send_time(self):
        auto_payouts.prepare_job_payouts(self.job.pk)
        ManagedJob.objects.filter(pk=self.job.pk).update(dispute_open=True)
        fake = FakeB2C()
        for row in self.payouts().values():
            self.assertEqual(auto_payouts.send_payout(row.pk, client=fake), "waiting")
        self.assertEqual(fake.calls, [])


@ON
class PayeeChecksTests(AutoPayoutBase):
    def test_unverified_phone_waits_with_reason_then_sends(self):
        UserProfile.objects.filter(user=self.manager).update(phone_verified_at=None)
        fake, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake.calls], ["shop"])
        manager_row = self.payouts()["manager"]
        self.assertEqual(manager_row.status, P.STATUS_PENDING)
        self.assertEqual(manager_row.waiting_reason, auto_payouts.WAIT_PHONE_UNVERIFIED)
        self.assertIn("phone", manager_row.waiting_detail)
        self.verify_phone(self.manager, "254711000001")
        fake2, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake2.calls], ["manager"])

    def test_capability_off_waits_with_reason(self):
        self.printer.capability_overrides = {"can_receive_payouts": False}
        self.printer.save()
        fake, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake.calls], ["manager"])
        shop_row = self.payouts()["shop"]
        self.assertEqual(shop_row.status, P.STATUS_PENDING)
        self.assertEqual(shop_row.waiting_reason, auto_payouts.WAIT_CANNOT_RECEIVE_PAYOUTS)

    def test_admin_list_shows_waiting_reason(self):
        UserProfile.objects.filter(user=self.manager).update(phone_verified_at=None)
        self.run_payouts()
        response = self.as_admin().get("/api/admin/payouts/", {"waiting_reason": auto_payouts.WAIT_PHONE_UNVERIFIED})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["recipient_role"], "manager")


@ON
class RetryTests(AutoPayoutBase):
    def test_rejections_back_off_then_needs_review(self):
        fake = FakeB2C(B2CNotSent("Initiator invalid"))
        now = timezone.now()
        auto_payouts.run_auto_payouts(client=fake, now=now)
        row = self.payouts()["shop"]
        self.assertEqual(row.status, P.STATUS_FAILED)
        self.assertEqual(row.next_attempt_at, now + timedelta(seconds=300))
        calls_after_first = len(fake.calls)
        auto_payouts.run_auto_payouts(client=fake, now=now + timedelta(seconds=10))
        self.assertEqual(len(fake.calls), calls_after_first)  # not due yet
        auto_payouts.run_auto_payouts(client=fake, now=now + timedelta(seconds=301))
        row.refresh_from_db()
        self.assertEqual(row.status, P.STATUS_FAILED)
        self.assertEqual(row.attempt_count, 2)
        self.assertEqual(row.next_attempt_at, now + timedelta(seconds=301 + 600))
        auto_payouts.run_auto_payouts(client=fake, now=now + timedelta(seconds=301 + 601))
        row.refresh_from_db()
        self.assertEqual(row.attempt_count, 3)
        self.assertEqual(row.status, P.STATUS_NEEDS_REVIEW)
        self.assertEqual(row.waiting_reason, auto_payouts.REVIEW_RETRIES_EXHAUSTED)
        before = len(fake.calls)
        auto_payouts.run_auto_payouts(client=fake, now=now + timedelta(days=1))
        self.assertEqual(len(fake.calls), before)
        keys = [c["key"] for c in fake.calls if c["occasion"] == "shop"]
        self.assertEqual(len(keys), len(set(keys)))

    def test_ambiguous_send_goes_straight_to_review_and_is_never_retried(self):
        fake = FakeB2C(B2CAmbiguous("read timeout"))
        self.run_payouts(fake)
        for row in self.payouts().values():
            self.assertEqual(row.status, P.STATUS_NEEDS_REVIEW)
            self.assertEqual(row.waiting_reason, auto_payouts.REVIEW_AMBIGUOUS_SEND)
        self.run_payouts(fake, now=timezone.now() + timedelta(days=2))
        self.assertEqual(len(fake.calls), 2)

    def test_failure_result_callback_schedules_retry(self):
        self.run_payouts()
        row = self.payouts()["shop"]
        self.assertEqual(auto_payouts.process_b2c_callback(result_body(row, code=2001)), "failed")
        row.refresh_from_db()
        self.assertEqual(row.status, P.STATUS_FAILED)
        self.assertIsNotNone(row.next_attempt_at)

    def test_admin_retry_endpoint(self):
        self.run_payouts(FakeB2C(B2CNotSent("down")))
        row = self.payouts()["shop"]
        response = self.as_admin().post(f"/api/admin/payouts/{row.pk}/retry/", {"reason": "Daraja back up"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        row.refresh_from_db()
        self.assertEqual(row.status, P.STATUS_PENDING)
        fake, _ = self.run_payouts()
        self.assertIn("shop", [c["occasion"] for c in fake.calls])
        self.assertEqual(self.as_admin().post(f"/api/admin/payouts/{row.pk}/retry/", {}, format="json").status_code, 400)

    def test_stuck_processing_goes_to_review(self):
        now = timezone.now()
        self.run_payouts(now=now)
        auto_payouts.run_auto_payouts(client=FakeB2C(), now=now + timedelta(minutes=61))
        self.assertEqual({r.waiting_reason for r in self.payouts().values()}, {auto_payouts.REVIEW_RESULT_TIMEOUT})

    def test_cancelled_job_cancels_unsent_payouts_but_never_a_possibly_paid_one(self):
        self.run_payouts(FakeB2C(B2CAmbiguous("read timeout")))  # both rows: needs_review / ambiguous_send
        shop = self.payouts()["shop"]
        P.objects.filter(pk=shop.pk).update(status=P.STATUS_PENDING, waiting_reason="")
        ManagedJob.objects.filter(pk=self.job.pk).update(status=ManagedJobStatus.CANCELLED)
        fake, _ = self.run_payouts()
        self.assertEqual(fake.calls, [])
        rows = self.payouts()
        self.assertEqual(rows["shop"].status, P.STATUS_CANCELLED)
        self.assertEqual(rows["manager"].status, P.STATUS_NEEDS_REVIEW)
        self.assertEqual(rows["manager"].waiting_reason, auto_payouts.REVIEW_AMBIGUOUS_SEND)


@ON
class CallbackSecurityTests(AutoPayoutBase):
    def setUp(self):
        super().setUp()
        self.run_payouts()
        self.row = self.payouts()["shop"]

    def assert_unchanged(self):
        before = (self.row.status, self.row.transfer_reference, len(self.row.audit_log), self.row.b2c_transaction_id)
        self.row.refresh_from_db()
        self.assertEqual(
            (self.row.status, self.row.transfer_reference, len(self.row.audit_log), self.row.b2c_transaction_id),
            before,
        )
        self.assertEqual(self.row.status, P.STATUS_PROCESSING)

    def test_unsigned_callback_is_refused(self):
        response = self.post_callback(result_body(self.row), signed=False)
        self.assertEqual(response.status_code, 403)
        self.assert_unchanged()

    def test_wrong_secret_is_refused(self):
        response = APIClient().post(
            "/api/payments/mpesa/b2c/result/not-the-secret/", result_body(self.row), format="json",
            REMOTE_ADDR=DARAJA_TEST_PEER_IP,
        )
        self.assertEqual(response.status_code, 403)
        self.assert_unchanged()

    def test_non_safaricom_ip_is_refused(self):
        response = self.post_callback(result_body(self.row), ip="203.0.113.9")
        self.assertEqual(response.status_code, 403)
        self.assert_unchanged()

    def test_spoofed_forwarded_for_is_not_trusted(self):
        response = APIClient().post(
            daraja_callback_path("/api/payments/mpesa/b2c/result/"), result_body(self.row), format="json",
            REMOTE_ADDR="203.0.113.9", HTTP_X_FORWARDED_FOR=DARAJA_TEST_PEER_IP,
        )
        self.assertEqual(response.status_code, 403)
        self.assert_unchanged()

    def test_unknown_originator_id_changes_nothing(self):
        response = self.post_callback(result_body(self.row, occ="PRINTY-PO999999-A1"))
        self.assertEqual(response.status_code, 200)
        self.assert_unchanged()

    def test_conversation_id_mismatch_changes_nothing(self):
        self.assertEqual(auto_payouts.process_b2c_callback(result_body(self.row, conversation_id="AG_FORGED")), "mismatch")
        self.assert_unchanged()

    def test_wrong_amount_changes_nothing(self):
        self.assertEqual(auto_payouts.process_b2c_callback(result_body(self.row, amount=Decimal("8000"))), "rejected")
        self.assert_unchanged()

    def test_missing_receipt_changes_nothing(self):
        self.assertEqual(auto_payouts.process_b2c_callback(result_body(self.row, receipt="")), "rejected")
        self.assert_unchanged()

    def test_old_attempt_key_changes_nothing(self):
        self.assertEqual(
            auto_payouts.process_b2c_callback(result_body(self.row, occ=f"PRINTY-PO{self.row.pk}-A0")), "unknown"
        )
        self.assert_unchanged()

    def test_queue_timeout_goes_to_review(self):
        body = {"Result": {"OriginatorConversationID": self.row.idempotency_key,
                           "ConversationID": self.row.b2c_conversation_id, "ResultCode": 1, "ResultDesc": "timeout"}}
        response = self.post_callback(body, kind="timeout")
        self.assertEqual(response.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, P.STATUS_NEEDS_REVIEW)
        self.assertEqual(self.row.waiting_reason, auto_payouts.REVIEW_QUEUE_TIMEOUT)

    def test_late_success_after_timeout_review_still_reconciles(self):
        now = timezone.now()
        auto_payouts.sweep_result_timeouts(now=now + timedelta(hours=2))
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, P.STATUS_NEEDS_REVIEW)
        self.assertEqual(auto_payouts.process_b2c_callback(result_body(self.row)), "released")


@ON
class LimitsTests(AutoPayoutBase):
    @override_settings(AUTO_PAYOUT_MAX_AMOUNT=500)
    def test_over_per_payout_max_goes_to_review(self):
        fake, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake.calls], ["manager"])
        row = self.payouts()["shop"]
        self.assertEqual(row.status, P.STATUS_NEEDS_REVIEW)
        self.assertEqual(row.waiting_reason, auto_payouts.REVIEW_OVER_MAX)

    @override_settings(AUTO_PAYOUT_DAILY_CAP=1000)
    def test_daily_cap_goes_to_review_then_admin_approval_sends(self):
        fake, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake.calls], ["manager"])
        row = self.payouts()["shop"]
        self.assertEqual(row.waiting_reason, auto_payouts.REVIEW_DAILY_CAP)
        response = self.as_admin().post(
            f"/api/admin/payouts/{row.pk}/approve/",
            {"confirm_amount": "800.00", "reason": "Month-end batch, checked."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        fake2, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake2.calls], ["shop"])

    @override_settings(AUTO_PAYOUT_DAILY_CAP=1000)
    def test_cap_counts_only_today(self):
        self.run_payouts()
        ManagedJobPayout.objects.update(sent_at=timezone.now() - timedelta(days=1))
        row = self.payouts()["shop"]
        row.status = P.STATUS_PENDING
        row.waiting_reason = ""
        row.save()
        fake, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake.calls], ["shop"])


@ON
class ApprovalTests(AutoPayoutBase):
    def setUp(self):
        super().setUp()
        ManagedJob.objects.filter(pk=self.job.pk).update(completion_method=ManagedJob.COMPLETION_BUYER_NO_CODE)
        self.run_payouts()
        self.row = self.payouts()["shop"]

    def test_legacy_no_code_needs_approval_then_follows_normal_path(self):
        self.assertEqual(self.row.status, P.STATUS_NEEDS_REVIEW)
        self.assertEqual(self.row.waiting_reason, auto_payouts.REVIEW_LEGACY_NO_CODE)
        response = self.as_admin().post(
            f"/api/admin/payouts/{self.row.pk}/approve/",
            {"confirm_amount": "800", "reason": "Buyer confirmed by phone."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, P.STATUS_PENDING)
        self.assertEqual(self.row.reviewed_by, self.admin)
        entry = self.row.audit_log[-1]
        self.assertEqual(entry["reason"], "Buyer confirmed by phone.")
        self.assertEqual(entry["confirmed_amount"], "800.00")
        self.assertTrue(
            LogEntry.objects.filter(
                object_id=str(self.row.pk),
                change_message__contains='"action": "payout_approve_review"',
            ).filter(change_message__contains="Buyer confirmed by phone.").exists()
        )
        fake, _ = self.run_payouts()
        self.assertEqual([c["occasion"] for c in fake.calls], ["shop"])
        self.assertEqual(self.payouts()["manager"].status, P.STATUS_NEEDS_REVIEW)

    def test_approval_requires_exact_amount_and_reason(self):
        api = self.as_admin()
        url = f"/api/admin/payouts/{self.row.pk}/approve/"
        self.assertEqual(api.post(url, {"confirm_amount": "799", "reason": "x"}, format="json").json()["code"],
                         "amount_mismatch")
        self.assertEqual(api.post(url, {"confirm_amount": "800"}, format="json").json()["code"], "reason_required")
        self.assertEqual(api.post(url, {"reason": "x"}, format="json").json()["code"], "amount_confirmation_required")
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, P.STATUS_NEEDS_REVIEW)

    def test_possibly_paid_review_needs_confirm_not_paid(self):
        P.objects.filter(pk=self.row.pk).update(waiting_reason=auto_payouts.REVIEW_AMBIGUOUS_SEND)
        api = self.as_admin()
        url = f"/api/admin/payouts/{self.row.pk}/approve/"
        refused = api.post(url, {"confirm_amount": "800", "reason": "checked"}, format="json")
        self.assertEqual(refused.json()["code"], "confirm_not_paid_required")
        ok = api.post(url, {"confirm_amount": "800", "reason": "Statement shows no transfer",
                            "confirm_not_paid": True}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_data_problems_cannot_be_approved(self):
        P.objects.filter(pk=self.row.pk).update(waiting_reason=auto_payouts.REVIEW_SPLIT_EXCEEDS_TOTAL)
        response = self.as_admin().post(
            f"/api/admin/payouts/{self.row.pk}/approve/", {"confirm_amount": "800", "reason": "x"}, format="json"
        )
        self.assertEqual(response.json()["code"], "not_approvable")

    def test_only_super_admin_can_use_payout_admin_endpoints(self):
        staff = User.objects.create_user(email="ap-staff@example.com", password="x", is_staff=True)
        for user in (self.client_user, self.manager, self.printer, staff):
            api = self.as_admin(user)
            with self.subTest(user=user.email):
                self.assertEqual(api.get("/api/admin/payouts/").status_code, 403)
                self.assertEqual(api.post(f"/api/admin/payouts/{self.row.pk}/approve/",
                                          {"confirm_amount": "800", "reason": "x"}, format="json").status_code, 403)
                self.assertEqual(api.post(f"/api/admin/payouts/{self.row.pk}/retry/",
                                          {"reason": "x"}, format="json").status_code, 403)
                self.assertEqual(api.get(f"/api/admin/jobs/{self.job.pk}/money-summary/").status_code, 403)
                # /api/payouts/mine/ is read-only for everyone.
                self.assertEqual(api.post("/api/payouts/mine/", {}, format="json").status_code, 405)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, P.STATUS_NEEDS_REVIEW)


@ON
class ManualPathSharesTheStateMachineTests(AutoPayoutBase):
    def test_manual_release_refused_while_b2c_in_flight(self):
        self.run_payouts()
        with self.assertRaises(ValidationError):
            release_managed_job_payouts(managed_job=self.job, released_by=self.admin)
        self.assertEqual(P.objects.filter(status=P.STATUS_RELEASED).count(), 0)

    def test_manual_release_refused_for_needs_review(self):
        ManagedJob.objects.filter(pk=self.job.pk).update(completion_method=ManagedJob.COMPLETION_BUYER_NO_CODE)
        self.run_payouts()
        with self.assertRaises(ValidationError):
            release_managed_job_payouts(managed_job=self.job, released_by=self.admin)

    def test_manual_release_of_pending_auto_row_reuses_the_row_and_logs(self):
        UserProfile.objects.filter(user=self.manager).update(phone_verified_at=None)
        UserProfile.objects.filter(user=self.printer).update(phone_verified_at=None)
        self.run_payouts()
        result = release_managed_job_payouts(managed_job=self.job, released_by=self.admin)
        self.assertEqual(len(result["payouts"]), 2)
        self.assertEqual(P.objects.count(), 2)
        for row in P.objects.all():
            self.assertEqual(row.status, P.STATUS_RELEASED)
            self.assertEqual(row.disbursement_mode, P.DISBURSEMENT_MANUAL)
            self.assertIn("pending->released", [entry["event"] for entry in row.audit_log])
        fake, _ = self.run_payouts()
        self.assertEqual(fake.calls, [])

    def test_dispute_release_only_clears_the_hold_when_auto_is_on(self):
        ManagedJob.objects.filter(pk=self.job.pk).update(
            status=ManagedJobStatus.DISPUTED, dispute_open=True, payout_hold=True,
            exception_status=ManagedJobExceptionStatus.DISPUTE_OPEN,
        )
        response = self.as_admin().post(
            f"/api/admin/disputes/{self.job.pk}/resolve/",
            {"resolution": "release", "reason": "Buyer withdrew the complaint."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["released"], [])
        self.assertFalse(P.objects.exists())
        self.job.refresh_from_db()
        self.assertFalse(self.job.payout_hold)
        self.assertEqual(self.job.status, ManagedJobStatus.COMPLETED)
        fake, _ = self.run_payouts()
        self.assertEqual(len(fake.calls), 2)


class DisputeReleaseManualWhenOffTests(AutoPayoutBase):
    def test_dispute_release_blocked_by_in_flight_payout_is_400_and_rolled_back(self):
        P.objects.create(managed_job=self.job, recipient_role=P.RECIPIENT_ROLE_SHOP, recipient=self.printer,
                         amount=SHOP_PAYOUT, status=P.STATUS_PROCESSING)
        ManagedJob.objects.filter(pk=self.job.pk).update(
            status=ManagedJobStatus.DISPUTED, dispute_open=True,
            exception_status=ManagedJobExceptionStatus.DISPUTE_OPEN,
        )
        response = self.as_admin().post(
            f"/api/admin/disputes/{self.job.pk}/resolve/", {"resolution": "release", "reason": "x"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "release_blocked")
        self.job.refresh_from_db()
        self.assertTrue(self.job.dispute_open)
        self.assertEqual(self.job.status, ManagedJobStatus.DISPUTED)

    def test_dispute_release_still_releases_manually_when_auto_is_off(self):
        ManagedJob.objects.filter(pk=self.job.pk).update(
            status=ManagedJobStatus.DISPUTED, dispute_open=True,
            exception_status=ManagedJobExceptionStatus.DISPUTE_OPEN,
        )
        response = self.as_admin().post(
            f"/api/admin/disputes/{self.job.pk}/resolve/",
            {"resolution": "release", "reason": "Resolved."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["released"]), 2)
        self.assertEqual(P.objects.filter(status=P.STATUS_RELEASED, disbursement_mode=P.DISBURSEMENT_MANUAL).count(), 2)


@ON
class MoneySummaryAndVisibilityTests(AutoPayoutBase):
    def test_money_summary_is_read_only_and_shows_holds(self):
        self.job.rush_premium = Decimal("100.00")
        self.job.rush_premium_status = "held"
        self.job.save()
        self.run_payouts()
        before = list(P.objects.values_list("status", flat=True))
        response = self.as_admin().get(f"/api/admin/jobs/{self.job.pk}/money-summary/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["totals"]["client_paid"], "1200.00")
        self.assertEqual(body["totals"]["shop_payout"], "800.00")
        self.assertEqual(body["totals"]["manager_payout"], "240.00")
        self.assertEqual(body["totals"]["printy_fee"], "160.00")
        self.assertEqual(body["totals"]["unallocated"], "0.00")
        self.assertTrue(body["totals"]["payouts_within_client_total"])
        self.assertEqual(body["holds"]["rush_premium_status"], "held")
        self.assertFalse(body["holds"]["rush_premium_blocks_auto_payout"])
        self.assertEqual(len(body["payouts"]), 2)
        self.assertEqual(list(P.objects.values_list("status", flat=True)), before)
        self.assertEqual(self.as_admin().post(f"/api/admin/jobs/{self.job.pk}/money-summary/").status_code, 405)

    def test_my_payouts_shop_sees_own_manager_theirs_client_none(self):
        self.run_payouts()
        shop_rows = self.as_admin(self.printer).get("/api/payouts/mine/").json()["results"]
        self.assertEqual([r["recipient_role"] for r in shop_rows], ["shop"])
        self.assertNotIn("1200", str(shop_rows))
        manager_rows = self.as_admin(self.manager).get("/api/payouts/mine/").json()["results"]
        self.assertEqual([r["recipient_role"] for r in manager_rows], ["manager"])
        self.assertEqual(self.as_admin(self.client_user).get("/api/payouts/mine/").status_code, 403)
        for row in shop_rows + manager_rows:
            self.assertNotIn("audit_log", row)
            self.assertNotIn("recipient_id", row)


@ON
class TotalNeverExceedsClientTotalTests(AutoPayoutBase):
    def test_fee_floor_boundaries(self):
        from pricing.services.platform_fee_policy import calculate_financial_split, minimum_manager_markup

        policy = self.policy
        costs = [
            policy.under_production_threshold - 1,
            policy.under_production_threshold,
            policy.under_production_threshold + 1,
            policy.high_production_threshold,
            policy.high_production_threshold + 1,
        ]
        ManagedJob.objects.update(status=ManagedJobStatus.CANCELLED)
        for cost in costs:
            for waived in (False, True):
                with self.subTest(cost=str(cost), waived=waived):
                    cost = Decimal(cost).quantize(Decimal("1"))
                    markup = minimum_manager_markup(production_cost=cost, policy=policy, fee_waived=waived)
                    result = calculate_financial_split(
                        production_cost=cost, manager_markup=markup, policy=policy, fee_waived=waived
                    )
                    job = self.make_job(
                        tag=f"floor-{cost}-{waived}", shop_payout=result.shop_payout,
                        manager_payout=result.manager_payout, client_total=result.client_total,
                        printy_fee=result.printy_fee,
                    )
                    fake, _ = self.run_payouts()
                    sent = sum((Decimal(c["amount"]) for c in fake.calls), Decimal("0"))
                    rows = P.objects.filter(managed_job=job)
                    queued = sum((r.amount for r in rows), Decimal("0"))
                    self.assertLessEqual(queued, result.client_total)
                    self.assertLessEqual(sent, result.client_total)
                    self.assertEqual(queued + result.printy_fee, result.client_total)
                    ManagedJob.objects.filter(pk=job.pk).update(status=ManagedJobStatus.CANCELLED)


class ProductionConfigTests(TestCase):
    def test_config_errors_list_every_missing_b2c_setting(self):
        errors = auto_payout_config_errors({"MPESA_CALLBACK_SECRET": ""}, production=True)
        joined = " ".join(errors)
        for name in ("MPESA_B2C_SHORTCODE", "MPESA_B2C_INITIATOR_NAME", "MPESA_B2C_SECURITY_CREDENTIAL",
                     "MPESA_B2C_RESULT_URL", "MPESA_B2C_TIMEOUT_URL", "MPESA_CALLBACK_SECRET"):
            self.assertIn(name, joined)

    def test_http_callback_urls_refused_in_production(self):
        values = {name: "set-value" for name in ("MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_B2C_SHORTCODE",
                                                 "MPESA_B2C_INITIATOR_NAME", "MPESA_B2C_SECURITY_CREDENTIAL")}
        values.update(MPESA_B2C_RESULT_URL="http://x/r/", MPESA_B2C_TIMEOUT_URL="http://x/t/",
                      MPESA_CALLBACK_SECRET="s" * 40, AUTO_PAYOUT_MAX_AMOUNT=1, AUTO_PAYOUT_DAILY_CAP=1)
        self.assertTrue(any("https" in e for e in auto_payout_config_errors(values, production=True)))
        self.assertFalse(auto_payout_config_errors(values, production=False))

    def test_production_refuses_to_boot_with_auto_payouts_but_no_b2c_settings(self):
        from tests.test_audit_config_proxy_trust import PRODUCTION_ENV, _boot

        output = _boot({**PRODUCTION_ENV, "AUTO_PAYOUTS_ENABLED": "true"})
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:AUTO_PAYOUTS_ENABLED is on but automatic payouts", output)

    def test_production_boots_with_auto_payouts_when_fully_configured(self):
        from tests.test_audit_config_proxy_trust import PRODUCTION_ENV, _boot

        env = {
            **PRODUCTION_ENV,
            "AUTO_PAYOUTS_ENABLED": "true",
            "MPESA_CONSUMER_KEY": "boot-test-consumer-key",
            "MPESA_CONSUMER_SECRET": "boot-test-consumer-secret",
            "MPESA_CALLBACK_URL": "https://api.printy.ke/api/payments/mpesa/callback/",
            "MPESA_B2C_SHORTCODE": "600000",
            "MPESA_B2C_INITIATOR_NAME": "boot-test-initiator",
            "MPESA_B2C_SECURITY_CREDENTIAL": "boot-test-security-credential",
        }
        self.assertIn("BOOT_OK", _boot(env))

    def test_production_default_is_off(self):
        from tests.test_audit_config_proxy_trust import PRODUCTION_ENV, _boot

        self.assertIn("BOOT_OK", _boot(PRODUCTION_ENV))


class PayoutStateMachineTests(AutoPayoutBase):
    """Each layer is tested on its own: the edge table and the manual guard."""

    def row(self, status):
        P.objects.filter(managed_job=self.job).delete()
        return P.objects.create(managed_job=self.job, recipient_role=P.RECIPIENT_ROLE_SHOP,
                                recipient=self.printer, amount=SHOP_PAYOUT, status=status)

    def test_only_a_b2c_callback_may_mark_a_transfer_released(self):
        from jobs.payout_state import (SOURCE_ADMIN_REVIEW, SOURCE_AUTO, SOURCE_B2C_CALLBACK, SOURCE_MANUAL,
                                       PayoutTransitionError, transition)

        for status in (P.STATUS_PROCESSING, P.STATUS_NEEDS_REVIEW):
            for source in (SOURCE_MANUAL, SOURCE_AUTO, SOURCE_ADMIN_REVIEW):
                with self.subTest(status=status, source=source):
                    with self.assertRaises(PayoutTransitionError):
                        transition(self.row(status), P.STATUS_RELEASED, source=source)
            transition(self.row(status), P.STATUS_RELEASED, source=SOURCE_B2C_CALLBACK)

    def test_final_states_never_move(self):
        from jobs.payout_state import SOURCE_B2C_CALLBACK, SOURCE_MANUAL, PayoutTransitionError, transition

        for status in (P.STATUS_RELEASED, P.STATUS_CANCELLED):
            for target in (P.STATUS_PENDING, P.STATUS_FAILED, P.STATUS_PROCESSING, P.STATUS_RELEASED):
                for source in (SOURCE_MANUAL, SOURCE_B2C_CALLBACK):
                    with self.subTest(status=status, target=target, source=source):
                        with self.assertRaises(PayoutTransitionError):
                            transition(self.row(status), target, source=source)

    def test_review_back_to_pending_needs_admin_review(self):
        from jobs.payout_state import SOURCE_ADMIN_REVIEW, SOURCE_AUTO, PayoutTransitionError, transition

        with self.assertRaises(PayoutTransitionError):
            transition(self.row(P.STATUS_NEEDS_REVIEW), P.STATUS_PENDING, source=SOURCE_AUTO)
        transition(self.row(P.STATUS_NEEDS_REVIEW), P.STATUS_PENDING, source=SOURCE_ADMIN_REVIEW)

    def test_manual_release_guard_on_its_own(self):
        from jobs.payout_state import PayoutTransitionError, assert_manual_release_allowed

        for status in (P.STATUS_PROCESSING, P.STATUS_NEEDS_REVIEW, P.STATUS_CANCELLED):
            with self.subTest(status=status), self.assertRaises(PayoutTransitionError):
                assert_manual_release_allowed(self.row(status))
        for status in (P.STATUS_PENDING, P.STATUS_FAILED, P.STATUS_ON_HOLD):
            assert_manual_release_allowed(self.row(status))

    def test_manual_release_path_calls_the_guard(self):
        from unittest import mock

        from jobs.payout_state import PayoutTransitionError

        with mock.patch("jobs.payout_services.assert_manual_release_allowed",
                        side_effect=PayoutTransitionError("blocked by guard")) as guard:
            with self.assertRaises(ValidationError):
                release_managed_job_payouts(managed_job=self.job, released_by=self.admin)
        self.assertTrue(guard.called)
        self.assertFalse(P.objects.filter(status=P.STATUS_RELEASED).exists())

    def test_failure_recording_never_downgrades_a_paid_or_in_flight_payout(self):
        from jobs.payout_services import record_payout_failure

        for status in (P.STATUS_RELEASED, P.STATUS_PROCESSING, P.STATUS_CANCELLED):
            with self.subTest(status=status):
                self.row(status)
                with self.assertRaises(ValidationError):
                    record_payout_failure(managed_job=self.job, role=P.RECIPIENT_ROLE_SHOP, reason="late failure")
                self.assertEqual(P.objects.get(managed_job=self.job).status, status)

    def test_every_transition_is_logged(self):
        from jobs.payout_state import SOURCE_AUTO, transition

        payout = self.row(P.STATUS_PENDING)
        transition(payout, P.STATUS_PROCESSING, source=SOURCE_AUTO, actor=self.admin, reason="b2c_request")
        entry = payout.audit_log[-1]
        self.assertEqual(entry["event"], "pending->processing")
        self.assertEqual(entry["source"], SOURCE_AUTO)
        self.assertEqual(entry["actor_id"], self.admin.pk)
