from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0013_quoteitemfinishing_selected_side_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QuoteRequestMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                ("sender_role", models.CharField(choices=[("client", "Client"), ("shop", "Shop"), ("system", "System")], default="system", help_text="Whether this message came from the client, shop, or system.", max_length=20, verbose_name="sender role")),
                ("message_kind", models.CharField(choices=[("status", "Status update"), ("question", "Question"), ("reply", "Reply"), ("rejection", "Rejection"), ("quote", "Quote"), ("note", "Note")], default="note", help_text="Thread message classification for UI timelines.", max_length=20, verbose_name="message kind")),
                ("body", models.TextField(blank=True, default="", help_text="Visible thread message body.", verbose_name="body")),
                ("metadata", models.JSONField(blank=True, help_text="Optional structured metadata for timeline rendering.", null=True, verbose_name="metadata")),
                ("quote_request", models.ForeignKey(help_text="Quote request this thread message belongs to.", on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="quotes.quoterequest", verbose_name="quote request")),
                ("sender", models.ForeignKey(blank=True, help_text="User who sent this message.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quote_request_messages", to=settings.AUTH_USER_MODEL, verbose_name="sender")),
                ("shop_quote", models.ForeignKey(blank=True, help_text="Optional linked quote revision for this message.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="messages", to="quotes.shopquote", verbose_name="shop quote")),
            ],
            options={
                "verbose_name": "quote request message",
                "verbose_name_plural": "quote request messages",
                "ordering": ["created_at", "id"],
            },
        ),
    ]
