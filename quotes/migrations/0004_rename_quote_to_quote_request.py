# Rename QuoteShareLink.quote → quote_request for clarity

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('quotes', '0003_add_quote_request_indexes'),
    ]

    operations = [
        migrations.RenameField(
            model_name='quotesharelink',
            old_name='quote',
            new_name='quote_request',
        ),
    ]
