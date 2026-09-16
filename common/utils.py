"""Common utility functions."""
from decimal import Decimal


def decimal_from_value(value):
    """Safely convert to Decimal. Returns None for None."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
