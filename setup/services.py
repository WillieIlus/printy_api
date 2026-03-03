"""
Setup status service — determines onboarding progress for printer/shop owners.

Approach: pricing_ready is computed live (Option A) and cached on Shop.pricing_ready
via refresh_pricing_ready(). Call refresh after pricing model changes.
"""
from catalog.choices import ProductStatus
from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, PrintingRate
from shops.models import Shop


def pricing_exists(shop: Shop) -> bool:
    """
    A shop has pricing when it has at least one active machine with
    at least one active printing rate, AND at least one active paper
    with selling_price > 0.
    """
    has_machine_with_rate = PrintingRate.objects.filter(
        machine__shop=shop, machine__is_active=True, is_active=True
    ).exists()
    has_paper = Paper.objects.filter(
        shop=shop, is_active=True, selling_price__gt=0
    ).exists()
    return has_machine_with_rate and has_paper


def refresh_pricing_ready(shop: Shop) -> bool:
    """Recompute and persist Shop.pricing_ready. Returns the new value."""
    ready = pricing_exists(shop)
    if shop.pricing_ready != ready:
        shop.pricing_ready = ready
        shop.save(update_fields=["pricing_ready", "updated_at"])
    return ready


def get_product_publish_check(product: Product) -> dict:
    """
    Check whether a product can be published.
    Returns {'can_publish': bool, 'block_reasons': [str]}.
    """
    reasons = []
    shop = product.shop

    if not pricing_exists(shop):
        reasons.append("Add at least one machine with printing rates and one paper with selling price.")

    if not product.name or not product.name.strip():
        reasons.append("Product name is required.")

    if product.pricing_mode == "SHEET":
        if not product.default_finished_width_mm or not product.default_finished_height_mm:
            reasons.append("Set default finished dimensions (width and height).")
    elif product.pricing_mode == "LARGE_FORMAT":
        if not product.default_finished_width_mm or not product.default_finished_height_mm:
            reasons.append("Set default dimensions for large format product.")

    return {
        "can_publish": len(reasons) == 0,
        "block_reasons": reasons,
    }


def get_setup_status(user) -> dict:
    """
    Return onboarding/setup status for a printer user.

    Response shape:
    {
        "has_shop": bool,
        "has_papers": bool,
        "has_machines": bool,
        "has_pricing": bool,
        "has_finishing": bool,
        "has_published_products": bool,
        "pricing_ready": bool,
        "next_step": "shop" | "papers" | "machines" | "pricing" | "products" | "done",
        "blocking_reason": str
    }
    """
    shop = Shop.objects.filter(owner=user).first()
    has_shop = shop is not None

    if not has_shop:
        return {
            "has_shop": False,
            "has_papers": False,
            "has_machines": False,
            "has_pricing": False,
            "has_finishing": False,
            "has_published_products": False,
            "pricing_ready": False,
            "next_step": "shop",
            "blocking_reason": "Create your print shop to get started.",
        }

    has_papers = Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0).exists()
    has_machines = Machine.objects.filter(shop=shop, is_active=True).exists()
    has_pricing = pricing_exists(shop)
    has_finishing = FinishingRate.objects.filter(shop=shop, is_active=True).exists()
    has_published = Product.objects.filter(shop=shop, status=ProductStatus.PUBLISHED).exists()

    refresh_pricing_ready(shop)

    if not has_machines:
        next_step = "machines"
        reason = "Add at least one printer/machine to your shop."
    elif not has_papers:
        next_step = "papers"
        reason = "Add paper stock with selling prices."
    elif not has_pricing:
        next_step = "pricing"
        reason = "Set printing rates for your machines."
    elif not has_published:
        next_step = "products"
        reason = "Create and publish at least one product."
    else:
        next_step = "done"
        reason = ""

    return {
        "has_shop": True,
        "has_papers": has_papers,
        "has_machines": has_machines,
        "has_pricing": has_pricing,
        "has_finishing": has_finishing,
        "has_published_products": has_published,
        "pricing_ready": shop.pricing_ready,
        "next_step": next_step,
        "blocking_reason": reason,
    }
