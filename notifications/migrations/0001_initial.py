# Create Notification model

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "notification_type",
                    models.CharField(
                        choices=[
                            ("quote_request_submitted", "Quote request submitted"),
                            ("shop_quote_sent", "Shop quote sent"),
                            ("shop_quote_accepted", "Shop quote accepted"),
                            ("shop_quote_declined", "Shop quote declined"),
                            ("order_created", "Order created"),
                        ],
                        max_length=50,
                        verbose_name="type",
                    ),
                ),
                (
                    "object_type",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="e.g. quote_request, shop_quote, production_order",
                        max_length=50,
                        verbose_name="object type",
                    ),
                ),
                (
                    "object_id",
                    models.PositiveIntegerField(blank=True, null=True, verbose_name="object id"),
                ),
                ("message", models.TextField(default="", verbose_name="message")),
                ("read_at", models.DateTimeField(blank=True, null=True, verbose_name="read at")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="user",
                    ),
                ),
            ],
            options={
                "verbose_name": "notification",
                "verbose_name_plural": "notifications",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(fields=["user", "-created_at"], name="notif_user_created_idx"),
        ),
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(fields=["user", "read_at"], name="notif_user_read_idx"),
        ),
    ]
