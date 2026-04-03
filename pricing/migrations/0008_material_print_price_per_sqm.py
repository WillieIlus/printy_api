from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pricing", "0007_alter_printingrate_duplex_surcharge_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="material",
            name="print_price_per_sqm",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Optional area-based print charge per square meter for large-format work.",
                max_digits=12,
                verbose_name="print price per sqm",
            ),
        ),
    ]
