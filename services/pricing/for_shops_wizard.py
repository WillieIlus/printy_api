from __future__ import annotations

from typing import Any


POSTPONED_DETAIL = "The legacy rate wizard is postponed for MVP; use the MVP rate-card setup."


def _postponed_payload() -> dict[str, Any]:
    return {
        "status": "postponed",
        "detail": POSTPONED_DETAIL,
        "steps": [],
        "fields": [],
    }


def build_rate_wizard_config(shop) -> dict[str, Any]:
    return _postponed_payload()


def build_public_rate_wizard_config() -> dict[str, Any]:
    return _postponed_payload()


def build_public_rate_wizard_preview(*, quantity: int, rates: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "postponed",
        "detail": POSTPONED_DETAIL,
        "quantity": quantity,
        "rates": rates,
        "can_calculate": False,
    }


def save_step_values(shop, step_key: str, values: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "postponed",
        "detail": POSTPONED_DETAIL,
        "step_key": step_key,
        "values": values,
    }


def build_step_preview(shop, step_key: str, *, quantity: int | None = None) -> dict[str, Any]:
    return {
        "status": "postponed",
        "detail": POSTPONED_DETAIL,
        "step_key": step_key,
        "quantity": quantity,
        "can_calculate": False,
    }


def complete_rate_wizard(shop) -> dict[str, Any]:
    return {
        "status": "postponed",
        "detail": POSTPONED_DETAIL,
    }
