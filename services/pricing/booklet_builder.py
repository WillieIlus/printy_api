from services.pricing.booklet import calculate_booklet_pricing


def build_booklet_preview(**kwargs):
    return calculate_booklet_pricing(**kwargs)
