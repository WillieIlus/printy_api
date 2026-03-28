from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0006_shopmembership"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="is_vat_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Whether VAT should be applied to quote calculations for this shop.",
                verbose_name="VAT enabled",
            ),
        ),
        migrations.AddField(
            model_name="shop",
            name="vat_mode",
            field=models.CharField(
                choices=[("inclusive", "Inclusive"), ("exclusive", "Exclusive")],
                default="exclusive",
                help_text="Whether prices returned by the pricing engine are VAT-inclusive or VAT-exclusive.",
                max_length=20,
                verbose_name="VAT mode",
            ),
        ),
        migrations.AddField(
            model_name="shop",
            name="vat_rate",
            field=models.DecimalField(
                decimal_places=2,
                default="16.00",
                help_text="VAT rate percentage applied when VAT is enabled.",
                max_digits=5,
                verbose_name="VAT rate",
            ),
        ),
    ]
