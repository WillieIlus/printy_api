# Add shop_quote FK to ProductionOrder; migrate status to new values

import django.db.models.deletion
from django.db import migrations, models


def migrate_production_order_status(apps, schema_editor):
    ProductionOrder = apps.get_model("production", "ProductionOrder")
    STATUS_MAP = {
        "DRAFT": "pending",
        "IN_PROGRESS": "in_progress",
        "COMPLETED": "completed",
        "CANCELLED": "cancelled",
    }
    for order in ProductionOrder.objects.all():
        new_status = STATUS_MAP.get(order.status, "pending")
        order.status = new_status
        order.save(update_fields=["status"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0006_add_shop_quote_model"),
        ("production", "0003_rename_client_to_customer"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionorder",
            name="shop_quote",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="production_orders",
                to="quotes.shopquote",
                verbose_name="shop quote",
            ),
        ),
        migrations.RunPython(migrate_production_order_status, noop),
        migrations.AlterField(
            model_name="productionorder",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("in_progress", "In Progress"),
                    ("ready", "Ready"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
                verbose_name="status",
            ),
        ),
    ]
