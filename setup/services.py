"""Backend truth for shop setup and rate-card readiness."""

from django.db import OperationalError, ProgrammingError

from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, PrintingRate
from shops.models import Shop

PROFILE_PLACEHOLDERS = {
    "description": "Business description for the shop.",
    "business_email": "shop@printy.ke",
    "phone_number": "+254 700 000 000",
    "address_line": "Street address",
}

READINESS_STEPS = [
    ("profile", "Public shop details", "Open profile"),
    ("materials", "Papers and materials", "Add papers"),
    ("pricing", "Print pricing rules", "Add pricing"),
    ("finishing", "Finishing rates", "Add finishing"),
    ("turnaround", "Turnaround guidance", "Add turnaround"),
    ("publish", "Publish your shop", "Review visibility"),
]

SHOP_STATUS_ONLY_FIELDS = [
    "id",
    "owner_id",
    "name",
    "description",
    "business_email",
    "phone_number",
    "address_line",
    "city",
    "state",
    "country",
    "service_area",
    "turnaround_statement",
    "public_whatsapp_number",
    "is_active",
    "is_public",
    "supports_custom_requests",
    "supports_catalog_requests",
]


def _dashboard_url(shop: Shop | None, step: str) -> str:
    if not shop:
        return "/dashboard/shops/create"
    mapping = {
        "profile": "/dashboard/shop/profile",
        "materials": "/dashboard/shop/materials",
        "pricing": "/dashboard/shop/pricing",
        "finishing": "/dashboard/shop/finishing",
        "turnaround": "/dashboard/shop/products",
        "publish": "/dashboard/shop/profile",
        "complete": "/dashboard/shop",
        "shop": "/dashboard/shop",
        "machines": "/dashboard/shop/pricing",
        "papers": "/dashboard/shop/materials",
        "products": "/dashboard/shop/products",
    }
    return mapping.get(step, "/dashboard/shop")


def _has_real_text(value: str | None, placeholder: str | None = None) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return False
    if placeholder and normalized == placeholder:
        return False
    return True

def _shop_profile_complete(shop: Shop) -> bool:
    return all(
        [
            _has_real_text(shop.name),
            _has_real_text(shop.description, PROFILE_PLACEHOLDERS["description"]),
            (
                _has_real_text(shop.business_email, PROFILE_PLACEHOLDERS["business_email"])
                or _has_real_text(shop.phone_number, PROFILE_PLACEHOLDERS["phone_number"])
            ),
            _has_real_text(shop.address_line, PROFILE_PLACEHOLDERS["address_line"]),
            _has_real_text(shop.city),
            _has_real_text(shop.country),
        ]
    )


def _product_turnaround_configured(shop: Shop) -> bool:
    return Product.objects.filter(
        shop=shop,
        is_active=True,
    ).filter(
        standard_turnaround_hours__isnull=False,
    ).exists() or Product.objects.filter(
        shop=shop,
        is_active=True,
    ).filter(
        rush_turnaround_hours__isnull=False,
    ).exists() or Product.objects.filter(
        shop=shop,
        is_active=True,
    ).filter(
        turnaround_days__isnull=False,
    ).exists()


def _turnaround_configured(shop: Shop) -> bool:
    return _has_real_text(getattr(shop, "turnaround_statement", "")) or _product_turnaround_configured(shop)


def _build_steps_payload(*, shop: Shop | None, state: dict, next_step: str) -> list[dict]:
    if not shop:
        return [
            {
                "key": "shop",
                "label": "Shop",
                "done": False,
                "accessible": True,
                "cta_label": "Create shop",
                "cta_url": "/dashboard/shops/create",
                "blocking_reason": "Create your shop first so Printy can track pricing readiness.",
            },
        ]

    step_done = {
        "profile": state["shop_profile_complete"],
        "materials": state["has_materials"],
        "pricing": state["has_pricing_rules"],
        "finishing": state["has_finishing_rates"],
        "turnaround": state["turnaround_configured"],
        "publish": state["shop_published"],
        "papers": state["has_materials"],
        "products": state["turnaround_configured"],
        "machines": state["has_pricing_rules"],
    }
    helpers = {
        "profile": "Complete your profile so buyers trust your shop.",
        "materials": "Add papers so Printy can match your shop to real jobs.",
        "pricing": "Add pricing rules so buyers can see accurate estimates.",
        "finishing": "Add finishing rates to reduce manual confirmation.",
        "turnaround": "Add a turnaround statement so buyers know how fast you usually respond.",
        "publish": "Publish your shop when you are ready for buyer traffic.",
    }

    steps = []
    for key, label, cta_label in READINESS_STEPS:
        done = step_done[key]
        is_current = key == next_step
        steps.append(
            {
                "key": key,
                "label": label,
                "done": done,
                "accessible": True,
                "cta_label": "Review" if done else ("Complete now" if is_current else cta_label),
                "cta_url": _dashboard_url(shop, key),
                "blocking_reason": "" if done else helpers[key],
            }
        )
    return steps


def _build_status_for_shop(shop: Shop | None) -> dict:
    if not shop:
        recommendations = [
            "Create a shop first so you can start building a rate card.",
        ]
        return {
            "has_shop": False,
            "has_machines": False,
            "has_papers": False,
            "has_materials": False,
            "materials_count": 0,
            "has_pricing": False,
            "has_pricing_rules": False,
            "pricing_rules_count": 0,
            "has_finishing": False,
            "has_finishing_rates": False,
            "finishing_rates_count": 0,
            "has_products": False,
            "shop_profile_complete": False,
            "turnaround_configured": False,
            "shop_published": False,
            "can_receive_requests": False,
            "can_price_requests": False,
            "rate_card_completeness": 0,
            "setup_percent": 0,
            "next_step": "shop",
            "next_url": "/dashboard/shops/create",
            "warnings": [],
            "recommendations": recommendations,
            "blocking_reason": recommendations[0],
            "completed_steps": [],
            "pending_steps": ["shop"],
            "steps": _build_steps_payload(shop=None, state={}, next_step="shop"),
        }

    machine_count = Machine.objects.filter(shop=shop, is_active=True).count()
    paper_count = Paper.objects.filter(shop=shop, is_active=True).count()
    material_count = 0
    pricing_rules_count = PrintingRate.objects.filter(machine__shop=shop, is_active=True).count()
    finishing_rates_count = FinishingRate.objects.filter(shop=shop, is_active=True).count()
    products_count = Product.objects.filter(shop=shop, is_active=True).count()

    has_machines = machine_count > 0
    has_papers = paper_count > 0
    has_materials = (paper_count + material_count) > 0
    has_pricing_rules = pricing_rules_count > 0 and paper_count > 0
    has_finishing_rates = finishing_rates_count > 0
    has_products = products_count > 0
    shop_profile_complete = _shop_profile_complete(shop)
    turnaround_configured = _turnaround_configured(shop)
    shop_published = bool(shop.is_active and shop.is_public)
    can_receive_requests = bool(shop_published and (shop.supports_custom_requests or shop.supports_catalog_requests))
    can_price_requests = bool(can_receive_requests and has_materials and has_pricing_rules)

    rate_card_completeness = (
        (35 if has_materials else 0)
        + (35 if has_pricing_rules else 0)
        + (20 if has_finishing_rates else 0)
        + (10 if turnaround_configured else 0)
    )
    setup_percent = (
        (25 if shop_profile_complete else 0)
        + (25 if has_materials else 0)
        + (20 if has_pricing_rules else 0)
        + (10 if has_finishing_rates else 0)
        + (10 if turnaround_configured else 0)
        + (10 if shop_published else 0)
    )

    warnings: list[str] = []
    recommendations: list[str] = []

    if not shop_profile_complete:
        recommendations.append("Complete your profile so buyers trust your shop.")
    if not has_materials:
        recommendations.append("Add papers so Printy can match your shop to real jobs.")
    if not has_pricing_rules:
        recommendations.append("Add pricing rules so buyers can see accurate estimates.")
    if can_receive_requests and not has_finishing_rates:
        warnings.append("Your shop can receive requests, but some add-ons still need manual confirmation until finishing rates are added.")
    elif not has_finishing_rates:
        recommendations.append("Add finishing rates to reduce manual confirmation.")
    if not turnaround_configured:
        recommendations.append("Add a turnaround statement so buyers know how fast you usually respond.")
    if not shop_published:
        recommendations.append("Publish your shop when you are ready for marketplace visibility.")

    if not shop_profile_complete:
        next_step = "profile"
    elif not has_materials:
        next_step = "materials"
    elif not has_pricing_rules:
        next_step = "pricing"
    elif not has_finishing_rates:
        next_step = "finishing"
    elif not turnaround_configured:
        next_step = "turnaround"
    elif not shop_published:
        next_step = "publish"
    else:
        next_step = "complete"

    completed_steps = [
        key for key, done in [
            ("profile", shop_profile_complete),
            ("materials", has_materials),
            ("pricing", has_pricing_rules),
            ("finishing", has_finishing_rates),
            ("turnaround", turnaround_configured),
            ("publish", shop_published),
        ] if done
    ]

    pending_steps = [
        key for key, _label, _cta in READINESS_STEPS if key not in completed_steps
    ]

    state = {
        "shop_profile_complete": shop_profile_complete,
        "has_materials": has_materials,
        "has_pricing_rules": has_pricing_rules,
        "has_finishing_rates": has_finishing_rates,
        "turnaround_configured": turnaround_configured,
        "shop_published": shop_published,
    }
    steps = _build_steps_payload(shop=shop, state=state, next_step=next_step)

    return {
        "has_shop": True,
        "has_machines": has_machines,
        "has_papers": has_papers,
        "has_materials": has_materials,
        "materials_count": paper_count + material_count,
        "has_pricing": has_pricing_rules,
        "has_pricing_rules": has_pricing_rules,
        "pricing_rules_count": pricing_rules_count,
        "has_finishing": has_finishing_rates,
        "has_finishing_rates": has_finishing_rates,
        "finishing_rates_count": finishing_rates_count,
        "has_rate_card": bool(has_materials and has_pricing_rules),
        "has_products": has_products,
        "shop_profile_complete": shop_profile_complete,
        "turnaround_configured": turnaround_configured,
        "shop_published": shop_published,
        "can_receive_requests": can_receive_requests,
        "can_price_requests": can_price_requests,
        "rate_card_completeness": rate_card_completeness,
        "setup_percent": setup_percent,
        "next_step": next_step,
        "next_url": _dashboard_url(shop, next_step),
        "warnings": warnings,
        "recommendations": recommendations,
        "blocking_reason": recommendations[0] if recommendations else (warnings[0] if warnings else ""),
        "completed_steps": completed_steps,
        "pending_steps": pending_steps,
        "steps": steps,
    }


def get_setup_status_for_user(user) -> dict:
    try:
        shop = Shop.objects.filter(owner=user).only(*SHOP_STATUS_ONLY_FIELDS).order_by("id").first()
    except (ProgrammingError, OperationalError):
        shop = None
    return _build_status_for_shop(shop)


def get_setup_status_for_shop(shop: Shop) -> dict:
    return _build_status_for_shop(shop)


def get_setup_status(user) -> dict:
    status = get_setup_status_for_user(user)
    return {
        "has_shop": status["has_shop"],
        "has_materials": status["has_materials"],
        "has_rate_card": status.get("has_rate_card", False),
        "has_papers": status["has_papers"],
        "has_machines": status["has_machines"],
        "has_pricing": status["has_pricing"],
        "has_finishing": status["has_finishing"],
        "has_published_products": status["shop_published"],
        "pricing_ready": status["can_price_requests"],
        "next_step": "done" if status["next_step"] == "complete" else status["next_step"],
        "completion_percent": status["rate_card_completeness"],
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
