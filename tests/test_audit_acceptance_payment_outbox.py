"""Audit #8: acceptance, payment and outbox state.

Each test failed on the code before audit #8:

* PaymentInitiateSTKView accepted the quote (closing the request, rejecting
  its siblings) and only then refused payment for unapproved artwork;
* accept_quote_for_payment ignored a pending due-date revision;
* create_quote_message overwrote email_status SENT with NOT_SENT whenever it
  ran outside a transaction (on_commit fires immediately in autocommit);
* initiate_stk_push did its in-flight check without a row lock, reused a
  stale prompt forever, fetched a Daraja token on every push and stamped
  Daraja timestamps with the OS clock / UTC;
* notify() sent SMTP inside the caller's transaction.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from notifications.models import Notification
from notifications.services import notify
from payments.models import MpesaSTKRequest, Payment
from payments.services import (
    MpesaDarajaClient,
    create_payment_for_quote,
    handle_stk_callback,
    initiate_stk_push,
)
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.acceptance import accept_quote_for_payment
from quotes.artwork_review import mark_manager_holds
from quotes.choices import QuoteStatus
from quotes.messaging import create_quote_message
from quotes.models import EmailOutbox, Quote, QuoteRequest, QuoteRequestMessage
from shops.models import Shop

PHONE = "254712345678"
STUB = {"MPESA_FORCE_STUB": "1"}


def _quote_setup(test, *, prefix="a8"):
    test.client_user = User.objects.create_user(
        email=f"{prefix}-client@test.com", password="pass12345", role="client", name="A8 Client"
    )
    test.owner = User.objects.create_user(
        email=f"{prefix}-owner@test.com", password="pass12345", role="shop_owner", name="A8 Owner"
    )
    test.shop = Shop.objects.create(owner=test.owner, name=f"{prefix} shop", slug=f"{prefix}-shop", is_active=True)
    test.quote_request = QuoteRequest.objects.create(
        shop=test.shop,
        created_by=test.client_user,
        customer_name="A8 Client",
        customer_email=test.client_user.email,
        customer_phone=PHONE,
        status=QuoteRequest.QUOTED,
    )
    test.quote = Quote.objects.create(
        quote_request=test.quote_request, shop=test.shop, created_by=test.owner, total=Decimal("1600.00"), status=Quote.SENT
    )
    create_quote_financial_split(
        quote=test.quote, production_cost=Decimal("1000.00"), broker_client_price=Decimal("1600.00")
    )


# ---------------------------------------------------------------------------
# Artwork gate before acceptance; date_updated blocks acceptance
# ---------------------------------------------------------------------------


@override_settings(**STUB)
class AcceptanceGateTests(TestCase):
    def setUp(self):
        _quote_setup(self)
        self.api = APIClient()
        self.api.force_authenticate(self.client_user)

    def test_stk_view_checks_artwork_before_accepting_the_quote(self):
        response = self.api.post("/api/payments/stk-push/", {"quote_id": self.quote.id, "phone_number": PHONE}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("artwork_payment_block_reason", response.json())
        self.quote.refresh_from_db()
        self.quote_request.refresh_from_db()
        self.assertEqual(self.quote.status, Quote.SENT)
        self.assertNotEqual(self.quote_request.status, QuoteStatus.CLOSED)
        self.assertIsNone(self.quote.accepted_at)
        self.assertFalse(Payment.objects.filter(quote=self.quote).exists())

    def test_stk_view_accepts_and_pushes_once_artwork_is_approved(self):
        mark_manager_holds(self.quote_request, actor=self.owner)
        response = self.api.post("/api/payments/stk-push/", {"quote_id": self.quote.id, "phone_number": PHONE}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, Quote.ACCEPTED)

    def test_pending_date_revision_blocks_acceptance(self):
        QuoteRequest.objects.filter(pk=self.quote_request.pk).update(
            date_updated=True, date_updated_new=timezone.now() + dt.timedelta(days=5)
        )
        with self.assertRaisesMessage(ValidationError, "due date"):
            accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user, payer_phone=PHONE)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, Quote.SENT)
        self.assertFalse(Payment.objects.filter(quote=self.quote).exists())

    def test_acknowledged_date_revision_allows_acceptance(self):
        QuoteRequest.objects.filter(pk=self.quote_request.pk).update(date_updated=False)
        quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user, payer_phone=PHONE)
        self.assertEqual(quote.status, Quote.ACCEPTED)
        self.assertIsNotNone(payment.pk)


# ---------------------------------------------------------------------------
# initiate_stk_push: lock, stale prompts, late callbacks
# ---------------------------------------------------------------------------


@override_settings(**STUB, MPESA_STK_IN_FLIGHT_SECONDS=180)
class StkInFlightTests(TestCase):
    def setUp(self):
        _quote_setup(self)
        self.payment = create_payment_for_quote(quote=self.quote, payer=self.client_user, payer_phone=PHONE)

    def _age(self, stk, seconds):
        MpesaSTKRequest.objects.filter(pk=stk.pk).update(requested_at=timezone.now() - dt.timedelta(seconds=seconds))

    def test_payment_row_is_locked_for_the_in_flight_check(self):
        original = Payment.objects.select_for_update
        with mock.patch.object(Payment.objects, "select_for_update", side_effect=original) as locked:
            initiate_stk_push(payment=self.payment, phone_number=PHONE)
        locked.assert_called()

    def test_fresh_prompt_still_blocks_a_conflicting_one(self):
        first = initiate_stk_push(payment=self.payment, phone_number=PHONE)
        self._age(first, 30)
        second = initiate_stk_push(payment=self.payment, phone_number=PHONE)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(MpesaSTKRequest.objects.filter(payment=self.payment).count(), 1)

    def test_stale_sent_prompt_is_expired_and_replaced(self):
        first = initiate_stk_push(payment=self.payment, phone_number=PHONE)
        self.assertEqual(first.status, MpesaSTKRequest.STATUS_SENT)
        self._age(first, 600)
        second = initiate_stk_push(payment=self.payment, phone_number=PHONE)
        self.assertNotEqual(second.pk, first.pk)
        first.refresh_from_db()
        self.assertEqual(first.status, MpesaSTKRequest.STATUS_EXPIRED)
        self.assertIsNotNone(first.completed_at)
        self.assertEqual(second.status, MpesaSTKRequest.STATUS_SENT)

    def test_stale_pending_prompt_from_a_crashed_request_is_expired(self):
        stuck = MpesaSTKRequest.objects.create(
            payment=self.payment, phone_number=PHONE, amount=self.payment.amount, status=MpesaSTKRequest.STATUS_PENDING
        )
        self._age(stuck, 600)
        fresh = initiate_stk_push(payment=self.payment, phone_number=PHONE)
        self.assertNotEqual(fresh.pk, stuck.pk)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, MpesaSTKRequest.STATUS_EXPIRED)

    def test_callers_payment_instance_is_kept_in_step(self):
        initiate_stk_push(payment=self.payment, phone_number=PHONE)
        self.assertEqual(self.payment.status, Payment.STATUS_PROCESSING)

    def _callback(self, stk, result_code, amount=None, receipt="RCPT123"):
        callback = {"CheckoutRequestID": stk.checkout_request_id, "ResultCode": result_code, "ResultDesc": "desc"}
        if result_code == 0:
            callback["CallbackMetadata"] = {
                "Item": [
                    {"Name": "MpesaReceiptNumber", "Value": receipt},
                    {"Name": "Amount", "Value": str(amount if amount is not None else self.payment.amount)},
                ]
            }
        return {"Body": {"stkCallback": callback}}

    def test_late_success_for_an_expired_prompt_is_still_applied(self):
        first = initiate_stk_push(payment=self.payment, phone_number=PHONE)
        self._age(first, 600)
        initiate_stk_push(payment=self.payment, phone_number=PHONE)  # expires `first`
        result = handle_stk_callback(callback_payload=self._callback(first, 0))
        self.assertEqual(result["status"], "success", result)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)

    def test_late_failure_for_an_expired_prompt_leaves_the_payment_alone(self):
        first = initiate_stk_push(payment=self.payment, phone_number=PHONE)
        self._age(first, 600)
        second = initiate_stk_push(payment=self.payment, phone_number=PHONE)
        handle_stk_callback(callback_payload=self._callback(first, 1032))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PROCESSING)
        second.refresh_from_db()
        self.assertEqual(second.status, MpesaSTKRequest.STATUS_SENT)


# ---------------------------------------------------------------------------
# Daraja OAuth token cache and timestamps
# ---------------------------------------------------------------------------


def _token_response(token="tok-1", expires_in="3599"):
    response = mock.Mock(status_code=200)
    response.json.return_value = {"access_token": token, "expires_in": expires_in}
    return response


class DarajaClientTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_token_is_cached_until_shortly_before_expiry(self):
        with mock.patch("payments.services.requests.get", return_value=_token_response()) as get:
            first = MpesaDarajaClient().get_access_token()
            second = MpesaDarajaClient().get_access_token()
        self.assertEqual((first, second), ("tok-1", "tok-1"))
        self.assertEqual(get.call_count, 1)

    def test_cache_lifetime_is_expires_in_minus_a_margin(self):
        with mock.patch("payments.services.requests.get", return_value=_token_response(expires_in="3599")), mock.patch(
            "django.core.cache.cache.set"
        ) as cache_set:
            MpesaDarajaClient().get_access_token()
        self.assertEqual(cache_set.call_args.kwargs["timeout"], 3599 - MpesaDarajaClient.TOKEN_EXPIRY_MARGIN_SECONDS)

    def test_expired_cache_fetches_a_new_token(self):
        with mock.patch("payments.services.requests.get", return_value=_token_response("tok-1")) as get:
            MpesaDarajaClient().get_access_token()
            cache.clear()  # what the cache does once the timeout passes
            get.return_value = _token_response("tok-2")
            self.assertEqual(MpesaDarajaClient().get_access_token(), "tok-2")
        self.assertEqual(get.call_count, 2)

    def test_daraja_timestamps_use_django_time_in_nairobi(self):
        instant = dt.datetime(2026, 1, 1, 21, 30, 5, tzinfo=dt.timezone.utc)  # 00:30:05 EAT on Jan 2
        from payments import services as payment_services

        with mock.patch("django.utils.timezone.now", return_value=instant):
            self.assertEqual(payment_services.daraja_timestamp(), "20260102003005")
            _password, timestamp = MpesaDarajaClient().generate_password()
            from mpesa_payments.services import _timestamp

            legacy = _timestamp()
        self.assertEqual(timestamp, "20260102003005")
        self.assertEqual(legacy, "20260102003005")


# ---------------------------------------------------------------------------
# Quote message email outbox: autocommit and atomic
# ---------------------------------------------------------------------------


@override_settings(EMAIL_OUTBOX_AUTO_SEND=True, EMAIL_OUTBOX_SEND_ASYNC=False)
class QuoteMessageOutboxTransactionTests(TransactionTestCase):
    def setUp(self):
        _quote_setup(self, prefix="a8tx")

    def _message(self):
        return create_quote_message(
            quote_request=self.quote_request,
            sender=self.owner,
            recipient=self.client_user,
            sender_role=QuoteRequestMessage.SenderRole.SHOP,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
            message_kind=QuoteRequestMessage.MessageKind.QUOTE,
            message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
            direction=QuoteRequestMessage.Direction.INBOUND,
            subject="Quote update",
            body="Your quote is ready.",
            send_email_copy=True,
        )

    @mock.patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_autocommit_send_is_not_overwritten_to_not_sent(self, send):
        message = self._message()
        send.assert_called_once_with(fail_silently=False)
        outbox = EmailOutbox.objects.get(message=message)
        self.assertEqual(outbox.status, EmailOutbox.Status.SENT)
        stored = QuoteRequestMessage.objects.get(pk=message.pk)
        self.assertEqual(stored.email_status, QuoteRequestMessage.EmailStatus.SENT)
        self.assertTrue(stored.email_sent)
        # The returned object reflects the stored outcome too.
        self.assertEqual(message.email_status, QuoteRequestMessage.EmailStatus.SENT)

    @mock.patch("quotes.messaging.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp down"))
    def test_autocommit_failure_is_not_overwritten_to_not_sent(self, send):
        message = self._message()
        stored = QuoteRequestMessage.objects.get(pk=message.pk)
        self.assertEqual(stored.email_status, QuoteRequestMessage.EmailStatus.FAILED)
        self.assertEqual(stored.email_error, "smtp down")

    @mock.patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_atomic_send_happens_after_commit_and_sticks(self, send):
        with transaction.atomic():
            message = self._message()
            send.assert_not_called()
            self.assertFalse(EmailOutbox.objects.filter(message=message).exists())
        send.assert_called_once_with(fail_silently=False)
        stored = QuoteRequestMessage.objects.get(pk=message.pk)
        self.assertEqual(stored.email_status, QuoteRequestMessage.EmailStatus.SENT)
        self.assertEqual(EmailOutbox.objects.get(message=message).status, EmailOutbox.Status.SENT)

    @mock.patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_rolled_back_transaction_sends_nothing(self, send):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self._message()
                raise RuntimeError("rollback")
        send.assert_not_called()
        self.assertFalse(EmailOutbox.objects.exists())

    @mock.patch("quotes.messaging._create_email_outbox_entry", side_effect=RuntimeError("db hiccup"))
    def test_outbox_creation_failure_after_commit_does_not_raise_into_the_caller(self, _create):
        with transaction.atomic():
            message = self._message()
        stored = QuoteRequestMessage.objects.get(pk=message.pk)
        self.assertEqual(stored.email_status, QuoteRequestMessage.EmailStatus.FAILED)


# ---------------------------------------------------------------------------
# notify(): SMTP after commit, never inside the caller's transaction
# ---------------------------------------------------------------------------


class NotifyEmailAfterCommitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a8-notify@test.com", password="pass12345", role="client")

    def _notify(self):
        return notify(
            recipient=self.user,
            notification_type=Notification.PAYMENT_CONFIRMED,
            message="Paid",
            object_type="payment",
            object_id=42,
            send_email_notification=True,
            email_subject="Printy - Payment Confirmed",
            email_message="Your payment is confirmed.",
        )

    def test_email_is_not_sent_inside_the_transaction(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with transaction.atomic():
                self._notify()
                self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(len(callbacks), 1)

    def test_email_is_sent_once_the_transaction_commits(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._notify()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Printy - Payment Confirmed")

    def test_smtp_failure_is_logged_not_raised(self):
        with mock.patch("notifications.services.send_mail", side_effect=OSError("smtp down")):
            with self.assertLogs("notifications.services", level="WARNING"):
                with self.captureOnCommitCallbacks(execute=True):
                    notification = self._notify()
        self.assertIsNotNone(notification.pk)


class NotifyRollbackTransactionTests(TransactionTestCase):
    def test_rolled_back_change_sends_no_email(self):
        user = User.objects.create_user(email="a8-rollback@test.com", password="pass12345", role="client")
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                notify(
                    recipient=user,
                    notification_type=Notification.PAYMENT_CONFIRMED,
                    message="Paid",
                    object_type="payment",
                    object_id=7,
                    send_email_notification=True,
                )
                raise RuntimeError("payment rolled back")
        self.assertEqual(len(mail.outbox), 0)
