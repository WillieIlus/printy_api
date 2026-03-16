# Add optional QuoteRequest.customer FK for unified customer linking

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0003_rename_client_to_customer'),
        ('quotes', '0004_rename_quote_to_quote_request'),
    ]

    operations = [
        migrations.AddField(
            model_name='quoterequest',
            name='customer',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional link to unified customer record (for repeat customers).',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='quote_requests',
                to='production.customer',
                verbose_name='customer',
            ),
        ),
    ]
