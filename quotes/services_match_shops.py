"""
Match shops by buyer specs. Public endpoint for "buyer chooses specs first, then sees matching shops".
"""
from django.db.models import Q

from inventory.models import Paper
from pricing.models import FinishingRate, PrintingRate
from shops.models import Shop


def find_shops_for_spec(
    *,
    pricing_mode: str,
    finished_width_mm: int = 0,
    finished_height_mm: int = 0,
    quantity: int = 100,
    sides: str = "SIMPLEX",
    color_mode: str = "COLOR",
    sheet_size: str = "",
    paper_gsm: int | None = None,
    paper_type: str = "",
    finishing_ids: list[int] | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 50,
) -> list:
    """
    Return shops that can fulfill the given spec.
    A shop matches when it has:
    - pricing_ready=True, is_active=True
    - For SHEET: Paper (sheet_size, gsm, paper_type), PrintingRate (sheet_size, color_mode)
    - For LARGE_FORMAT: Material, dimensions fit
    - All required finishing rates
    """
    qs = Shop.objects.filter(is_active=True, pricing_ready=True).exclude(
        Q(slug__isnull=True) | Q(slug="")
    )

    if pricing_mode == "SHEET":
        paper_filter = Q(
            papers__is_active=True,
            papers__selling_price__gt=0,
        )
        if sheet_size:
            paper_filter &= Q(papers__sheet_size=sheet_size)
        if paper_gsm:
            paper_filter &= Q(papers__gsm=paper_gsm)
        if paper_type:
            paper_filter &= Q(papers__paper_type__icontains=paper_type)

        qs = qs.filter(paper_filter).filter(
            machines__is_active=True,
            machines__printing_rates__sheet_size=sheet_size or "SRA3",
            machines__printing_rates__color_mode=color_mode,
            machines__printing_rates__is_active=True,
        )

    elif pricing_mode == "LARGE_FORMAT":
        qs = qs.filter(
            materials__is_active=True,
            materials__selling_price__gt=0,
        )

    if finishing_ids:
        for fid in finishing_ids:
            qs = qs.filter(
                finishing_rates__id=fid,
                finishing_rates__is_active=True,
            )

    qs = qs.distinct()

    if lat is not None and lng is not None and radius_km > 0:
        from common.geo import haversine_km

        qs = qs.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        )
        shops_with_dist = []
        for shop in qs:
            dist = haversine_km(lat, lng, float(shop.latitude), float(shop.longitude))
            if dist <= radius_km:
                shops_with_dist.append((shop, dist))
        shops_with_dist.sort(key=lambda x: x[1])
        return [s[0] for s in shops_with_dist]

    return list(qs[:50])


def build_match_result(shop: Shop, *, can_calculate: bool = True, reason: str = "", missing_fields: list[str] | None = None) -> dict:
    """Build a single shop match result for API response."""
    return {
        "id": shop.id,
        "name": shop.name,
        "slug": shop.slug,
        "can_calculate": can_calculate,
        "reason": reason or ("Ready to price" if can_calculate else "Pricing setup incomplete"),
        "missing_fields": missing_fields or [],
    }
