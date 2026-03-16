# Rename Client → Customer, ProductionOrder.client → customer

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0002_rename_job_to_production_order'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Client',
            new_name='Customer',
        ),
        migrations.RenameField(
            model_name='productionorder',
            old_name='client',
            new_name='customer',
        ),
    ]
