"""Simulate a Safaricom STK callback for development (sandbox only).

The buyer dashboard previously faked M-Pesa outcomes purely in the browser
(never touching a payment record). Real callbacks run through
``mpesa_payments.services.process_callback`` — this command feeds a synthetic
Safaricom callback body through that same handler, so every outcome drives
exactly the same downstream logic a real callback does (payment transitions,
reconciliation status, custody/settlement signals).

Run it only against the sandbox environment:

    python manage.py simulate_mpesa_callback --outcome=success --payment=<id>
    python manage.py simulate_mpesa_callback --outcome=cancelled --order=<id>
    python manage.py simulate_mpesa_callback --outcome=insufficient --order=<id>
    python manage.py simulate_mpesa_callback --outcome=amount-mismatch --order=<id>
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mpesa_payments.models import MpesaPayment
from mpesa_payments.services import process_callback

OUTCOMES = {
    "success": "ResultCode 0 with a matching amount — payment confirmed.",
    "insufficient": "ResultCode 2001 — payment failed (insufficient funds).",
    "cancelled": "ResultCode 1032 — payment cancelled by the user.",
    "amount-mismatch": "ResultCode 0 but a different amount — needs_review.",
}

OUTCOME_ALIASES = {
    "success": "success",
    "paid": "success",
    "insufficient": "insufficient",
    "failed": "insufficient",
    "failure": "insufficient",
    "insufficient-funds": "insufficient",
    "cancelled": "cancelled",
    "cancel": "cancelled",
    "user-cancelled": "cancelled",
    "amount-mismatch": "amount-mismatch",
    "amount_mismatch": "amount-mismatch",
    "mismatch": "amount-mismatch",
    "needs-review": "amount-mismatch",
    "needs_review": "amount-mismatch",
}


def _mpesa_env() -> str:
    return str(
        getattr(settings, "MPESA_ENV", "")
        or getattr(settings, "MPESA_ENVIRONMENT", "")
    ).lower()


class Command(BaseCommand):
    help = "Feed a synthetic Safaricom callback through the real STK callback handler (sandbox only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--outcome",
            required=True,
            help="One of: %s." % ", ".join(sorted(OUTCOMES)),
        )
        parser.add_argument(
            "--payment",
            type=int,
            default=None,
            help="ID of the MpesaPayment to simulate.",
        )
        parser.add_argument(
            "--order",
            type=int,
            default=None,
            help="ID of the payable object (job/quote) whose latest payable MpesaPayment to simulate.",
        )

    def handle(self, *args, **options):
        if _mpesa_env() != "sandbox":
            raise CommandError(
                "Simulated callbacks can only run when MPESA_ENV is 'sandbox' "
                "(current value: %s)." % (_mpesa_env() or "unset"),
            )

        outcome = OUTCOME_ALIASES.get(str(options["outcome"]).strip().lower())
        if outcome is None:
            raise CommandError(
                "Unknown outcome %r. Choose one of: %s." % (options["outcome"], ", ".join(sorted(OUTCOMES))),
            )

        if options["payment"] is None and options["order"] is None:
            raise CommandError("Pass one of --payment=<id> or --order=<id> (the payable's object id).")
        if options["payment"] is not None and options["order"] is not None:
            raise CommandError("Pass exactly one of --payment=<id> or --order=<id>.")

        if options["payment"] is not None:
            payment = MpesaPayment.objects.filter(pk=options["payment"]).first()
            if payment is None:
                raise CommandError("No M-Pesa payment found with id %s." % options["payment"])
        else:
            payment = (
                MpesaPayment.objects.filter(object_id=options["order"])
                .exclude(status__in=MpesaPayment.TERMINAL_STATES)
                .order_by("-initiated_at", "-id")
                .first()
            )
            if payment is None:
                raise CommandError(
                    "No non-terminal M-Pesa payment found for order id %s." % options["order"],
                )

        if payment.is_terminal:
            raise CommandError(
                "Payment %s is already %s — a late callback would be ignored. Use another payment."
                % (payment.id, payment.status),
            )

        if not payment.checkout_request_id:
            payment.checkout_request_id = f"TEST-SIM-CR-{payment.id}"
            update_fields = ["checkout_request_id"]
            if not payment.merchant_request_id:
                payment.merchant_request_id = f"TEST-SIM-MR-{payment.id}"
                update_fields.append("merchant_request_id")
            payment.save(update_fields=update_fields + ["updated_at"])

        payload = _callback_payload(payment=payment, outcome=outcome)
        entry = process_callback(payload)

        payment.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                "Simulated '%s' callback for payment %s -> status=%s reconciliation=%s"
                % (
                    outcome,
                    payment.id,
                    payment.status,
                    payment.reconciliation_status,
                )
                + (
                    " receipt=%s" % payment.mpesa_receipt_number
                    if payment.mpesa_receipt_number
                    else ""
                ),
            ),
        )
        if not entry.processed:
            self.stdout.write(
                self.style.WARNING(
                    "Callback was logged but not processed (duplicate/terminal). Simulate against a pending payment.",
                ),
            )


def _callback_payload(*, payment: MpesaPayment, outcome: str) -> dict:
    simulated_at = timezone.now()
    stamp = simulated_at.strftime("%Y%m%d%H%M%S")
    stk = {
        "CheckoutRequestID": payment.checkout_request_id,
        "MerchantRequestID": payment.merchant_request_id,
        "ResultCode": 0,
        "ResultDesc": "The service request is processed successfully.",
        "CallbackMetadata": {
            "Item": [
                {"Name": "Amount", "Value": str(payment.amount)},
                {"Name": "MpesaReceiptNumber", "Value": f"SANDBOX-{payment.id}"},
                {"Name": "TransactionDate", "Value": stamp},
                {"Name": "PhoneNumber", "Value": int(payment.phone_number) if payment.phone_number.isdigit() else payment.phone_number},
            ]
        },
    }

    if outcome == "insufficient":
        stk.update(
            {
                "ResultCode": 2001,
                "ResultDesc": "The balance is insufficient for this transaction",
            }
        )
        del stk["CallbackMetadata"]
    elif outcome == "cancelled":
        stk.update(
            {
                "ResultCode": 1032,
                "ResultDesc": "Request cancelled by user",
            }
        )
        del stk["CallbackMetadata"]
    elif outcome == "amount-mismatch":
        stk["CallbackMetadata"]["Item"][0]["Value"] = str(payment.amount + Decimal("1.00"))
        stk["ResultDesc"] = "The service request is processed successfully."

    return {
        "Body": {"stkCallback": stk},
        "SandboxSimulation": {
            "source": "management_command.simulate_mpesa_callback",
            "outcome": outcome,
            "simulated_at": simulated_at.isoformat(),
        },
    }