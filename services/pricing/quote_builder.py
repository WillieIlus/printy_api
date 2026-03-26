from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate
from services.pricing.engine import build_sheet_pricing


def build_quote_preview(*, shop, product_id: int, quantity: int, paper_id: int, machine_id: int, color_mode: str, sides: str, finishing_selections: list[dict] | None = None) -> dict:
    product = Product.objects.get(pk=product_id, shop=shop)
    paper = Paper.objects.get(pk=paper_id, shop=shop)
    machine = Machine.objects.get(pk=machine_id, shop=shop)

    finishings = []
    for selection in finishing_selections or []:
        rule = FinishingRate.objects.get(pk=selection["finishing_rate_id"], shop=shop, is_active=True)
        finishings.append(
            {
                "rule": rule,
                "selected_side": selection.get("selected_side", "both"),
            }
        )

    return build_sheet_pricing(
        product=product,
        quantity=quantity,
        paper=paper,
        machine=machine,
        color_mode=color_mode,
        sides=sides,
        finishings=finishings,
    )
