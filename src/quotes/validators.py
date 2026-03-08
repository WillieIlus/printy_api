"""Quote validation logic."""
from django.core.exceptions import ValidationError


def validate_quote_item(item) -> None:
    """
    Validate QuoteItem. Raises ValidationError if invalid.
    Extracted from QuoteItem.clean() for reuse.
    """
    if item.pricing_mode == "SHEET" and not item.paper_id:
        raise ValidationError({"paper": "Paper is required for SHEET pricing mode."})
    if item.pricing_mode == "LARGE_FORMAT":
        if not item.material_id:
            raise ValidationError({"material": "Material is required for LARGE_FORMAT."})
        if not item.chosen_width_mm or not item.chosen_height_mm:
            raise ValidationError(
                {"chosen_width_mm": "Dimensions required for LARGE_FORMAT."}
            )
    if item.paper_id and item.quote_request_id and item.paper.shop_id != item.quote_request.shop_id:
        raise ValidationError({"paper": "Paper must belong to the quote's shop."})
    if item.material_id and item.quote_request_id and item.material.shop_id != item.quote_request.shop_id:
        raise ValidationError({"material": "Material must belong to the quote's shop."})
    if item.machine_id and item.quote_request_id and item.machine.shop_id != item.quote_request.shop_id:
        raise ValidationError({"machine": "Machine must belong to the quote's shop."})


def validate_quantity(value: int, min_value: int = 1) -> None:
    """Validate quantity is positive."""
    if value is None or value < min_value:
        raise ValidationError({"quantity": f"Quantity must be >= {min_value}."})
