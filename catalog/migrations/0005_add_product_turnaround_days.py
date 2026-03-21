from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_add_productcategory_timestamps"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="turnaround_days",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Typical delivery or turnaround time for this product in business days.",
                null=True,
                verbose_name="delivery time (days)",
            ),
        ),
    ]
