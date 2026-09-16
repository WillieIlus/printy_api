"""
Location-based market pricing aggregation service.

Shop-owned pricing JSON was removed during the backend reset. Keep this
endpoint shape available, but do not treat shop setup data as market pricing.
"""
from .models import Shop

PRODUCT_SPECS = {
    "booklets": "50 units, A5, 300gsm, stapled",
    "flyers": "500 units, A5, 150gsm, uncut",
    "posters": "10 units, A2, 200gsm, uncut",
    "business_cards": "500 units, 250gsm, unlaminated",
}

PRODUCT_LABELS = {
    "booklets": "Booklets",
    "flyers": "A4 Flyers",
    "posters": "Posters",
    "business_cards": "Business Cards",
}

MIN_SAMPLE = 5


def get_location_pricing(location: str, fallback_to_city: bool = True) -> dict:
    """
    Returns aggregated market pricing for the given location.

    Args:
        location: area name, e.g. "Nairobi CBD" or "Nairobi"
        fallback_to_city: expand to city-level if local sample < MIN_SAMPLE

    Returns:
        dict matching the /api/shops/location-pricing/ response schema
    """
    base_qs = Shop.objects.filter(is_active=True)

    local_shops = base_qs.filter(city__iexact=location)
    shops_in_area = local_shops.count()

    active_shops = local_shops
    fallback_used = False
    fallback_location = None

    if shops_in_area < MIN_SAMPLE and fallback_to_city:
        city = location.split()[0]
        city_shops = base_qs.filter(city__icontains=city)
        if city_shops.count() > 0 and city_shops.count() > shops_in_area:
            active_shops = city_shops
            fallback_used = True
            fallback_location = city

    shops_count = active_shops.count()

    warning = "Location market pricing is unavailable until canonical pricing snapshots are modeled."
    if fallback_used:
        warning = (
            f"Only {shops_in_area} shop{'s' if shops_in_area != 1 else ''} found in {location}. "
            f"Showing {fallback_location}-wide shop availability instead. "
            "Location market pricing is unavailable until canonical pricing snapshots are modeled."
        )

    return {
        "location": fallback_location or location,
        "shops_in_location": shops_count,
        "pricing_data": {},
        "sufficient_data": False,
        "warning": warning,
        "fallback_location": fallback_location,
        "fallback_used": fallback_used,
    }
