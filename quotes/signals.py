"""Signals that keep law 10.2 datasets in sync without touching payment code.

The only writer of ``Payment.STATUS_PAID`` is the verified-confirmation path
(stdout callback/verification in ``payments.services.mark_payment_paid``). This
receiver observes that write and appends the immutable transaction row; it does
not initiate, confirm, or alter any payment.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(
    post_save,
    sender="payments.Payment",
    dispatch_uid="quotes.record_verified_payment_transaction",
)
def _record_verified_payment_transaction(sender, instance, **kwargs):
    from payments.models import Payment
    from quotes.transactions import record_verified_payment_transaction

    if (
        instance.status == Payment.STATUS_PAID
        and instance.confirmed_at is not None
    ):
        record_verified_payment_transaction(instance)
