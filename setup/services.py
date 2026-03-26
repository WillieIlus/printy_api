"""Backend truth for shop onboarding/setup progression."""

from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, Material, PrintingRate
from shops.models import Shop

SETUP_STEPS = [
    ("shop", "create shop"),
    ("materials", "add materials/papers"),
    ("pricing", "add pricing rules"),
    ("finishing", "add finishing rules"),
    ("products", "add first product"),
    ("complete", "setup complete"),
]


def _build_status_for_shop(shop: Shop | None) -> dict:
    if not shop:
        return {
            "has_shop": False,
            "has_materials": False,
            "has_pricing": False,
            "has_finishing": False,
            "has_products": False,
            "next_step": "shop",
            "next_url": "/dashboard/shops/create",
            "completed_steps": [],
            "pending_steps": [step for step, _ in SETUP_STEPS[:-1]],
        }

    has_materials = Paper.objects.filter(shop=shop, is_active=True).exists() or Material.objects.filter(shop=shop, is_active=True).exists()
    has_pricing = PrintingRate.objects.filter(machine__shop=shop, is_active=True).exists()
    has_finishing = FinishingRate.objects.filter(shop=shop, is_active=True).exists()
    has_products = Product.objects.filter(shop=shop, is_active=True).exists()

    if not has_materials:
        next_step = "materials"
    elif not has_pricing:
        next_step = "pricing"
    elif not has_finishing:
        next_step = "finishing"
    elif not has_products:
        next_step = "products"
    else:
        next_step = "complete"

    pending_steps = [code for code, _ in SETUP_STEPS if code not in {"complete"}]
    completed_steps = ["shop"]
    if has_materials:
        completed_steps.append("materials")
    if has_pricing:
        completed_steps.append("pricing")
    if has_finishing:
        completed_steps.append("finishing")
    if has_products:
        completed_steps.append("products")

    return {
        "has_shop": True,
        "has_materials": has_materials,
        "has_pricing": has_pricing,
        "has_finishing": has_finishing,
        "has_products": has_products,
        "next_step": next_step,
        "next_url": (
            f"/dashboard/shops/{shop.slug}" if next_step == "complete" else f"/dashboard/shops/{shop.slug}/{next_step}"
        ),
        "completed_steps": completed_steps,
        "pending_steps": [step for step in pending_steps if step not in completed_steps],
    }


def get_setup_status_for_user(user) -> dict:
    return _build_status_for_shop(Shop.objects.filter(owner=user).order_by("id").first())


def get_setup_status_for_shop(shop: Shop) -> dict:
    return _build_status_for_shop(shop)


def get_setup_status(user) -> dict:
    shop = Shop.objects.filter(owner=user).order_by("id").first()
    if not shop:
        return {
            "has_shop": False,
            "has_papers": False,
            "has_machines": False,
            "has_pricing": False,
            "has_finishing": False,
            "has_published_products": False,
            "pricing_ready": False,
            "next_step": "shop",
        }

    has_papers = Paper.objects.filter(shop=shop, is_active=True).exists()
    has_machines = Machine.objects.filter(shop=shop, is_active=True).exists()
    has_pricing = pricing_exists(shop)
    has_finishing = FinishingRate.objects.filter(shop=shop, is_active=True).exists()
    has_published_products = Product.objects.filter(shop=shop, status="PUBLISHED", is_active=True).exists()

    if not has_machines:
        next_step = "machines"
    elif not has_papers:
        next_step = "papers"
    elif not has_pricing:
        next_step = "pricing"
    elif not has_published_products and not has_finishing:
        next_step = "finishing"
    elif not has_published_products:
        next_step = "products"
    else:
        next_step = "done"

    return {
        "has_shop": True,
        "has_papers": has_papers,
        "has_machines": has_machines,
        "has_pricing": has_pricing,
        "has_finishing": has_finishing,
        "has_published_products": has_published_products,
        "pricing_ready": has_pricing,
        "next_step": next_step,
    }


def pricing_exists(shop: Shop) -> bool:
    return bool(
        PrintingRate.objects.filter(machine__shop=shop, is_active=True).exists()
        and Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0).exists()
    )


def get_product_publish_check(product: Product) -> dict:
    reasons = []
    if not product.name:
        reasons.append("Product name is required.")
    if not product.default_finished_width_mm or not product.default_finished_height_mm:
        reasons.append("Default finished dimensions are required.")
    if not Machine.objects.filter(shop=product.shop, is_active=True).exists():
        reasons.append("Add at least one machine before publishing products.")
    if not pricing_exists(product.shop):
        reasons.append("Add papers and printing rates before publishing products.")
    return {
        "can_publish": not reasons,
        "block_reasons": reasons,
    }
