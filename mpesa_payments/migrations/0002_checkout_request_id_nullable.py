"""checkout_request_id: "" -> NULL, null=True, still unique (audit #2).

With ``default=""`` + ``unique=True`` every payment row that never reached
Daraja held the same empty string, so the second failed/concurrent STK
initiation crashed with IntegrityError. NULLs do not collide on a UNIQUE
index (PostgreSQL and SQLite), so the column is made nullable first and the
existing empty strings are then converted to NULL. Reversible: NULL -> "" is
only safe while at most one such row exists, which is exactly the invariant
the old schema enforced, so the reverse path refuses otherwise.
"""

from django.db import migrations, models


def empty_to_null(apps, schema_editor):
    MpesaPayment = apps.get_model("mpesa_payments", "MpesaPayment")
    MpesaPayment.objects.filter(checkout_request_id="").update(checkout_request_id=None)


def null_to_empty(apps, schema_editor):
    MpesaPayment = apps.get_model("mpesa_payments", "MpesaPayment")
    nulls = MpesaPayment.objects.filter(checkout_request_id__isnull=True)
    if nulls.count() > 1:
        raise RuntimeError(
            "Cannot reverse: more than one MpesaPayment has no checkout_request_id, "
            "and the previous schema allowed only one empty value."
        )
    nulls.update(checkout_request_id="")


class Migration(migrations.Migration):
    dependencies = [
        ("mpesa_payments", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mpesapayment",
            name="checkout_request_id",
            field=models.CharField(blank=True, default=None, max_length=64, null=True, unique=True),
        ),
        migrations.RunPython(empty_to_null, null_to_empty),
    ]
