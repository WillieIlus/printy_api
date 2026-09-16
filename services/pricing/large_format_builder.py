from services.pricing.large_format import calculate_large_format_preview


def build_large_format_preview(
    *,
    shop,
    product_subtype: str,
    quantity: int,
    width_mm: int,
    height_mm: int,
    material,
    finishing_selections: list[dict] | None = None,
    hardware_finishing_rate=None,
    turnaround_hours: int | None = None,
) -> dict:
    finishings = []
    for selection in finishing_selections or []:
        finishings.append(
            {
                "rule": selection["finishing_rate"],
                "selected_side": selection.get("selected_side", "both"),
            }
        )

    return calculate_large_format_preview(
        shop=shop,
        product_subtype=product_subtype,
        quantity=quantity,
        width_mm=width_mm,
        height_mm=height_mm,
        material=material,
        finishing_selections=finishings,
        hardware_finishing_rate=hardware_finishing_rate,
        turnaround_hours=turnaround_hours,
    )
