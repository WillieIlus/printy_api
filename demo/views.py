"""
Public API for demo calculator — no auth required.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import DemoProduct
from .services import compute_demo_quote


@api_view(["GET"])
@permission_classes([AllowAny])
def demo_templates_list(request):
    """
    List all active demo products as templates for the calculator.
    Returns JSON matching the frontend DemoGalleryTemplate shape.
    """
    products = DemoProduct.objects.filter(is_active=True).prefetch_related(
        "product_finishing_options__finishing_rate"
    ).order_by("display_order", "name")

    templates = []
    for p in products:
        finishing_options = [
            {
                "finishing_rate": opt.finishing_rate_id,
                "is_default": opt.is_default,
                "price_adjustment": str(opt.price_adjustment) if opt.price_adjustment else None,
            }
            for opt in p.product_finishing_options.all()
        ]
        templates.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "category": p.category,
            "pricing_mode": p.pricing_mode,
            "default_finished_width_mm": p.default_finished_width_mm,
            "default_finished_height_mm": p.default_finished_height_mm,
            "default_sides": p.default_sides,
            "min_quantity": p.min_quantity,
            "default_sheet_size": p.default_sheet_size or "SRA3",
            "copies_per_sheet": p.copies_per_sheet,
            "min_gsm": p.min_gsm,
            "max_gsm": p.max_gsm,
            "finishing_options": finishing_options,
            "badge": p.badge or None,
        })
    return Response({"templates": templates})


@api_view(["POST"])
@permission_classes([AllowAny])
def demo_quote(request):
    """
    Compute a demo quote for a product.
    Body: { "product_id": int, "quantity": int, "sheet_size": str?, "gsm": int? }
    """
    product_id = request.data.get("product_id")
    quantity = request.data.get("quantity")

    if not product_id or quantity is None:
        return Response(
            {"error": "product_id and quantity are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        product = DemoProduct.objects.get(id=product_id, is_active=True)
    except DemoProduct.DoesNotExist:
        return Response(
            {"error": "Product not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    qty = int(quantity)
    if qty < product.min_quantity:
        return Response(
            {"error": f"Quantity must be at least {product.min_quantity}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    sheet_size = request.data.get("sheet_size")
    gsm = request.data.get("gsm")
    if gsm is not None:
        gsm = int(gsm)

    result = compute_demo_quote(
        product,
        qty,
        sheet_size=sheet_size,
        gsm=gsm,
    )
    return Response(result)
