from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="template_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Registry key used to render title/body per role and language.",
                max_length=64,
                verbose_name="template key",
            ),
        ),
        migrations.AddField(
            model_name="notification",
            name="params",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Structured values interpolated into the template at read time.",
                verbose_name="params",
            ),
        ),
        migrations.AddField(
            model_name="notification",
            name="priority",
            field=models.CharField(
                choices=[("action_required", "Action required"), ("informational", "Informational")],
                default="informational",
                help_text="``action_required`` items are pinned and accented in the UI.",
                max_length=20,
                verbose_name="priority",
            ),
        ),
        migrations.AddField(
            model_name="notification",
            name="idempotency_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Dedupe key such as (event_type, entity_id, recipient).",
                max_length=200,
                verbose_name="idempotency key",
            ),
        ),
        migrations.AlterField(
            model_name="notification",
            name="message",
            field=models.TextField(
                default="",
                help_text="Legacy fallback body, used when no template can be rendered.",
                verbose_name="message",
            ),
        ),
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.UniqueConstraint(
                condition=~Q(idempotency_key=""),
                fields=("user", "idempotency_key"),
                name="notif_user_idempotency_uniq",
            ),
        ),
    ]
