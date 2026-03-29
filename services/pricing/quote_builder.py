from services.pricing.engine import calculate_sheet_pricing


def build_quote_preview(
    *,
    shop,
    product=None,
    quantity: int,
    paper,
    machine,
    color_mode: str,
    sides: str,
    finishing_selections: list[dict] | None = None,
    width_mm: int | None = None,
    height_mm: int | None = None,
) -> dict:
    finishings = []
    for selection in finishing_selections or []:
        finishings.append(
            {
                "rule": selection["finishing_rate"],
                "selected_side": selection.get("selected_side", "both"),
            }
        )

    pricing = calculate_sheet_pricing(
        shop=shop,
        product=product,
        quantity=quantity,
        paper=paper,
        machine=machine,
        color_mode=color_mode,
        sides=sides,
        finishing_selections=finishings,
        width_mm=width_mm,
        height_mm=height_mm,
    )
    return pricing.to_dict()
