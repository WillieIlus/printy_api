"""Check an M-Pesa receipt with Daraja's Transaction Status Query.

Use this when a customer says they paid but the STK callback never arrived
("I have my receipt, my money is gone"). It asks Safaricom whether that receipt
settled, and reports what our own records hold for the same receipt.

The command is read-only. Daraja answers asynchronously to ``ResultURL``; that
notification is what may settle the payment, and it arrives whether or not an
operator runs this. Run it from an interactive shell, not cron:

    python manage.py mpesa_verify_receipt --receipt QG1234XY --phone 0712345678
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from mpesa_payments.models import MpesaPayment
from mpesa_payments.services import MpesaError, query_transaction_status


class Command(BaseCommand):
    help = "Verify an M-Pesa receipt with Daraja's Transaction Status Query (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--receipt", required=True, help="M-Pesa receipt / TransactionID.")
        parser.add_argument(
            "--phone",
            required=True,
            help="MSISDN that made the payment (PartyA), e.g. 0712345678.",
        )
        parser.add_argument("--remarks", default="Printy receipt verification")

    def handle(self, *args, **options):
        receipt = options["receipt"].strip()

        ours = MpesaPayment.objects.filter(mpesa_receipt_number=receipt).order_by("id")
        if not ours.exists():
            self.stdout.write(f"No payment in our records carries receipt {receipt}.")
        for payment in ours:
            self.stdout.write(
                f"  local payment_id={payment.id} status={payment.status} "
                f"amount={payment.amount} paid_amount={payment.paid_amount} "
                f"result={payment.result_code} {payment.result_desc}"
            )

        try:
            data = query_transaction_status(
                transaction_id=receipt,
                party_a=options["phone"],
                remarks=options["remarks"],
            )
        except MpesaError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"Daraja accepted the query (ResponseCode={data.get('ResponseCode')}): "
            f"{data.get('ResponseDescription')}"
        )
        self.stdout.write(
            "The verdict is delivered asynchronously to MPESA_RESULT_URL and applied "
            "automatically; re-check the payment record in a few seconds."
        )
