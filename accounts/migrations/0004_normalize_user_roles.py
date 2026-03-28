from django.db import migrations, models


def normalize_user_roles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Shop = apps.get_model("shops", "Shop")
    ShopMembership = apps.get_model("shops", "ShopMembership")

    owner_ids = set(Shop.objects.values_list("owner_id", flat=True))
    member_ids = set(
        ShopMembership.objects.filter(is_active=True).values_list("user_id", flat=True)
    )

    for user in User.objects.all().iterator():
        next_role = "client"
        if user.id in owner_ids or user.role in {"PRINTER", "shop_owner"}:
            next_role = "shop_owner"
        elif user.id in member_ids or user.role == "staff":
            next_role = "staff"
        elif user.role in {"CUSTOMER", "client"}:
            next_role = "client"

        if user.role != next_role:
            user.role = next_role
            user.save(update_fields=["role"])


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0006_shopmembership"),
        ("accounts", "0003_alter_user_role"),
    ]

    operations = [
        migrations.RunPython(normalize_user_roles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("client", "Client"),
                    ("shop_owner", "Shop Owner"),
                    ("staff", "Staff"),
                ],
                default="client",
                help_text="Primary account role used by the dashboard UI.",
                max_length=20,
            ),
        ),
    ]
