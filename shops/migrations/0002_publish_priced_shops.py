from django.db import migrations


def publish_priced_shops(apps, schema_editor):
    Shop = apps.get_model("shops", "Shop")
    Paper = apps.get_model("inventory", "Paper")
    PrintingRate = apps.get_model("pricing", "PrintingRate")

    priced_shop_ids = set(
        Paper.objects.filter(is_active=True, selling_price__gt=0)
        .values_list("shop_id", flat=True)
    )
    rated_shop_ids = set(
        PrintingRate.objects.filter(is_active=True, machine__is_active=True)
        .values_list("machine__shop_id", flat=True)
    )
    eligible_ids = priced_shop_ids & rated_shop_ids
    if eligible_ids:
        Shop.objects.filter(id__in=eligible_ids).update(is_active=True, is_public=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0001_initial"),
        ("inventory", "0001_initial"),
        ("pricing", "0002_shopratecardsetup"),
    ]

    operations = [
        migrations.RunPython(publish_priced_shops, noop_reverse),
    ]
