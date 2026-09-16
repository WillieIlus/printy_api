"""Signals so settlement code can react without importing this app.

Hook `mpesa_payment_confirmed` to release custody / confirm the job:

    from django.dispatch import receiver
    from mpesa_payments.signals import mpesa_payment_confirmed

    @receiver(mpesa_payment_confirmed)
    def release_custody(sender, payment, **kwargs):
        job = payment.payable
        if job is None:
            return
        ...
"""

import django.dispatch

# Sent once, only after a SUCCESS callback with a matching amount.
mpesa_payment_confirmed = django.dispatch.Signal()

# Sent once, after a failure or user cancellation.
mpesa_payment_failed = django.dispatch.Signal()
