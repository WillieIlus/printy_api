# Generated manually for production onboarding rate-card persistence.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pricing", "0001_initial"),
        ("shops", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShopRateCardSetup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, help_text="Timestamp when the record was created.", verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, help_text="Timestamp when the record was last updated.", verbose_name="updated at")),
                ("paper_rows", models.JSONField(blank=True, default=list)),
                ("finishing_rows", models.JSONField(blank=True, default=list)),
                ("shop_details", models.JSONField(blank=True, default=dict)),
                ("completed", models.BooleanField(default=False)),
                ("shop", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="rate_card_setup", to="shops.shop", verbose_name="shop")),
            ],
            options={
                "verbose_name": "shop rate card setup",
                "verbose_name_plural": "shop rate card setups",
                "ordering": ["shop__name"],
            },
        ),
    ]
