from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_draft_files_for_existing_drafts(apps, schema_editor):
    QuoteDraftFile = apps.get_model("quotes", "QuoteDraftFile")
    QuoteRequest = apps.get_model("quotes", "QuoteRequest")

    grouped_files = {}

    for draft in QuoteRequest.objects.filter(status="draft").order_by("created_by_id", "id"):
        key = (
            draft.created_by_id,
            (draft.customer_name or "").strip().lower(),
            (draft.customer_email or "").strip().lower(),
            (draft.customer_phone or "").strip(),
        )
        draft_file = grouped_files.get(key)
        if draft_file is None:
            company_name = (draft.customer_name or "").strip() or "Untitled Company"
            draft_file = QuoteDraftFile.objects.create(
                created_by_id=draft.created_by_id,
                company_name=company_name,
                contact_email=draft.customer_email or "",
                contact_phone=draft.customer_phone or "",
            )
            grouped_files[key] = draft_file

        draft.quote_draft_file_id = draft_file.id
        draft.save(update_fields=["quote_draft_file"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("quotes", "0009_add_print_quote_flow_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuoteDraftFile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company_name", models.CharField(default="Untitled Company", help_text="Top-level customer or company name used to group quote drafts.", max_length=255, verbose_name="company name")),
                ("contact_name", models.CharField(blank=True, default="", help_text="Optional contact person for this quote draft file.", max_length=255, verbose_name="contact name")),
                ("contact_email", models.EmailField(blank=True, help_text="Optional contact email for this quote draft file.", max_length=254, verbose_name="contact email")),
                ("contact_phone", models.CharField(blank=True, default="", help_text="Optional contact phone for this quote draft file.", max_length=50, verbose_name="contact phone")),
                ("notes", models.TextField(blank=True, default="", help_text="Shared notes for the grouped quote draft file.", verbose_name="notes")),
                ("status", models.CharField(choices=[("open", "Open"), ("closed", "Closed")], default="open", help_text="Open files can receive drafts from multiple shops. Closed files are read-only groupings.", max_length=20, verbose_name="status")),
                ("created_by", models.ForeignKey(help_text="User who owns this quote draft file.", on_delete=django.db.models.deletion.CASCADE, related_name="quote_draft_files", to=settings.AUTH_USER_MODEL, verbose_name="created by")),
            ],
            options={
                "verbose_name": "quote draft file",
                "verbose_name_plural": "quote draft files",
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
        migrations.AddField(
            model_name="quoterequest",
            name="quote_draft_file",
            field=models.ForeignKey(blank=True, help_text="Optional company-level grouping for active quote drafts.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="drafts", to="quotes.quotedraftfile", verbose_name="quote draft file"),
        ),
        migrations.AddIndex(
            model_name="quotedraftfile",
            index=models.Index(fields=["created_by", "status"], name="draft_file_user_status_idx"),
        ),
        migrations.RunPython(create_draft_files_for_existing_drafts, migrations.RunPython.noop),
    ]
