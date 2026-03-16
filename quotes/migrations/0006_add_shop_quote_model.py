# Add ShopQuote model; add shop_quote FK to QuoteItem; add shop_quote to QuoteShareLink

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0005_add_google_place_id"),
        ("quotes", "0005_add_quote_request_customer_fk"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ShopQuote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("sent", "Sent"),
                            ("revised", "Revised"),
                            ("accepted", "Accepted"),
                            ("declined", "Declined"),
                            ("expired", "Expired"),
                        ],
                        default="sent",
                        max_length=20,
                        verbose_name="status",
                    ),
                ),
                ("total", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name="total")),
                (
                    "pricing_locked_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="pricing locked at"),
                ),
                ("sent_at", models.DateTimeField(blank=True, null=True, verbose_name="sent at")),
                (
                    "whatsapp_message",
                    models.TextField(blank=True, default="", verbose_name="whatsapp message"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shop_quotes",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="created by",
                    ),
                ),
                (
                    "quote_request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shop_quotes",
                        to="quotes.quoterequest",
                        verbose_name="quote request",
                    ),
                ),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shop_quotes",
                        to="shops.shop",
                        verbose_name="shop",
                    ),
                ),
            ],
            options={
                "verbose_name": "shop quote",
                "verbose_name_plural": "shop quotes",
                "ordering": ["-sent_at", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="shopquote",
            index=models.Index(fields=["shop", "status"], name="shopquote_shop_status_idx"),
        ),
        migrations.AddIndex(
            model_name="shopquote",
            index=models.Index(fields=["quote_request", "-created_at"], name="shopquote_req_created_idx"),
        ),
        migrations.AddField(
            model_name="quoteitem",
            name="shop_quote",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="items",
                to="quotes.shopquote",
                verbose_name="shop quote",
            ),
        ),
        migrations.AddField(
            model_name="quotesharelink",
            name="shop_quote",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="share_links",
                to="quotes.shopquote",
                verbose_name="shop quote",
            ),
        ),
    ]
