from django.db import migrations


def backfill_quote_files_for_existing_requests(apps, schema_editor):
    QuoteDraftFile = apps.get_model("quotes", "QuoteDraftFile")
    QuoteRequest = apps.get_model("quotes", "QuoteRequest")

    grouped_files: dict[tuple, int] = {}

    queryset = (
        QuoteRequest.objects.filter(created_by_id__isnull=False, quote_draft_file_id__isnull=True)
        .select_related("customer")
        .order_by("created_by_id", "id")
    )

    for quote_request in queryset:
        customer = getattr(quote_request, "customer", None)
        company_name = ((customer.name if customer else "") or quote_request.customer_name or "").strip() or "Untitled Company"
        contact_name = (quote_request.customer_name or "").strip()
        contact_email = ((customer.email if customer else "") or quote_request.customer_email or "").strip().lower()
        contact_phone = ((customer.phone if customer else "") or quote_request.customer_phone or "").strip()

        key = (
            quote_request.created_by_id,
            company_name.lower(),
            contact_email,
            contact_phone,
        )

        draft_file_id = grouped_files.get(key)
        if draft_file_id is None:
            draft_file = QuoteDraftFile.objects.create(
                created_by_id=quote_request.created_by_id,
                company_name=company_name,
                contact_name=contact_name,
                contact_email=contact_email,
                contact_phone=contact_phone,
            )
            draft_file_id = draft_file.id
            grouped_files[key] = draft_file_id

        quote_request.quote_draft_file_id = draft_file_id
        quote_request.save(update_fields=["quote_draft_file"])


class Migration(migrations.Migration):
    dependencies = [
        ("quotes", "0010_quotedraftfile_and_request_grouping"),
    ]

    operations = [
        migrations.RunPython(backfill_quote_files_for_existing_requests, migrations.RunPython.noop),
    ]
