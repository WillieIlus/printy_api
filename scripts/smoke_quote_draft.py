#!/usr/bin/env python
"""
Smoke test: create draft, add product item, add custom item, preview-price, request-quote.
Run: python manage.py runscript scripts.smoke_calculator_draft
Or: python manage.py shell < scripts/smoke_calculator_draft.py (if runscript not installed)
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from decimal import Decimal

from accounts.models import User
from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, Material, PrintingRate
from quotes.models import QuoteItem, QuoteRequest
from quotes.services import build_preview_price_response, calculate_quote_item
from shops.models import Shop


def smoke_test():
    """Create minimal shop, product, paper; draft + items; preview; submit."""
    print("=== Quote Draft Smoke Test ===\n")

    # Get or create user
    user, _ = User.objects.get_or_create(
        username="smoke_buyer",
        defaults={"email": "smoke@test.ke", "is_active": True},
    )
    if not user.pk:
        user.set_password("test123")
        user.save()

    # Get or create shop (need owner for shop)
    owner = User.objects.filter().first() or user
    shop, _ = Shop.objects.get_or_create(
        slug="smoke-shop",
        defaults={
            "name": "Smoke Test Shop",
            "currency": "KES",
            "is_active": True,
            "owner": owner,
        },
    )

    # Get or create machine, paper, product
    machine = Machine.objects.filter(shop=shop).first()
    if not machine:
        machine = Machine.objects.create(
            shop=shop,
            name="Smoke Press",
            machine_type="OFFSET",
            max_width_mm=420,
            max_height_mm=594,
            is_active=True,
        )
    paper = Paper.objects.filter(shop=shop).first()
    if paper is None:
        paper = Paper.objects.create(
            shop=shop,
            sheet_size="A4",
            gsm=80,
            paper_type="COATED",
            width_mm=210,
            height_mm=297,
            buying_price=Decimal("1.00"),
            selling_price=Decimal("2.00"),
            is_active=True,
        )
    from pricing.choices import Sides

    product, _ = Product.objects.get_or_create(
        shop=shop,
        name="Smoke Flyer",
        defaults={
            "pricing_mode": "SHEET",
            "default_finished_width_mm": 100,
            "default_finished_height_mm": 150,
            "default_sides": Sides.SIMPLEX,
            "is_active": True,
        },
    )

    # Create printing rate if needed
    from pricing.choices import ColorMode

    if not PrintingRate.objects.filter(machine=machine).exists():
        PrintingRate.objects.create(
            machine=machine,
            sheet_size=paper.sheet_size,
            color_mode=ColorMode.COLOR,
            single_price=Decimal("1.00"),
            double_price=Decimal("1.50"),
            is_active=True,
        )

    # 1. Get or create draft
    draft = QuoteRequest.objects.filter(
        shop=shop, created_by=user, status="DRAFT"
    ).order_by("-created_at").first()
    if not draft:
        draft = QuoteRequest.objects.create(
            shop=shop, created_by=user, status="DRAFT"
        )
    print(f"1. Draft: {draft.id} (shop={shop.slug})")

    # 2. Add PRODUCT item
    item1, created = QuoteItem.objects.get_or_create(
        quote_request=draft,
        item_type="PRODUCT",
        product=product,
        defaults={
            "quantity": 100,
            "pricing_mode": "SHEET",
            "paper": paper,
            "machine": machine,
            "sides": "DUPLEX",
            "color_mode": "COLOR",
        },
    )
    print(f"2. Product item: {item1.id} ({'created' if created else 'exists'})")

    # 3. Add CUSTOM item (create new each run to avoid unique constraint)
    material = Material.objects.filter(shop=shop).first()
    item2 = QuoteItem.objects.create(
        quote_request=draft,
        item_type="CUSTOM",
        title="Custom banner",
        spec_text="2m x 1m vinyl",
        quantity=5,
        pricing_mode="LARGE_FORMAT" if material else "SHEET",
        chosen_width_mm=2000 if material else None,
        chosen_height_mm=1000 if material else None,
        material=material,
        paper=paper if not material else None,
        sides="SIMPLEX",
        color_mode="COLOR",
    )
    print(f"3. Custom item: {item2.id} (created)")

    # 4. Preview price
    preview = build_preview_price_response(draft)
    print(f"4. Preview: can_calculate={preview['can_calculate']}, total={preview['total']}")
    for line in preview.get("lines", []):
        print(f"   - {line}")
    if preview.get("needs_review_items"):
        print(f"   needs_review: {preview['needs_review_items']}")

    # 5. Request quote (submit)
    draft.status = "SUBMITTED"
    draft.save(update_fields=["status", "updated_at"])
    print(f"5. Request quote: status={draft.status}")

    print("\n=== Smoke test OK ===")


if __name__ == "__main__":
    smoke_test()
