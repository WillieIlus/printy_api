from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pricing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="wastepolicy",
            name="maximum_spoilage_rate",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("0.5000"),
                help_text=(
                    "Hard cap on billable spoilage. The maximum billable sheets are the theoretical "
                    "sheets plus this fraction of the theoretical sheets (rounded up)."
                ),
                max_digits=6,
            ),
        ),
    ]
