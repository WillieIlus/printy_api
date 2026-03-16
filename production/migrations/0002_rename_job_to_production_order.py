# Rename Job → ProductionOrder, JobProcess.job → production_order

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0001_initial'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Job',
            new_name='ProductionOrder',
        ),
        migrations.RenameField(
            model_name='jobprocess',
            old_name='job',
            new_name='production_order',
        ),
    ]
