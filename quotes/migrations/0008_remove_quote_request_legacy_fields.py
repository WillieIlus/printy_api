# Remove legacy fields from QuoteRequest; migrate QuoteShareLink to shop_quote only

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0007_migrate_data_to_shop_quote"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="quoterequest",
            name="total",
        ),
        migrations.RemoveField(
            model_name="quoterequest",
            name="pricing_locked_at",
        ),
        migrations.RemoveField(
            model_name="quoterequest",
            name="whatsapp_message",
        ),
        migrations.RemoveField(
            model_name="quoterequest",
            name="sent_at",
        ),
        migrations.AlterField(
            model_name="quoterequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("submitted", "Submitted"),
                    ("viewed", "Viewed"),
                    ("quoted", "Quoted"),
                    ("accepted", "Accepted"),
                    ("closed", "Closed"),
                    ("cancelled", "Cancelled"),
                ],
                default="draft",
                max_length=20,
                verbose_name="status",
            ),
        ),
        migrations.RemoveField(
            model_name="quotesharelink",
            name="quote_request",
        ),
        migrations.AlterField(
            model_name="quotesharelink",
            name="shop_quote",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="share_links",
                to="quotes.shopquote",
                verbose_name="shop quote",
            ),
        ),
    ]
