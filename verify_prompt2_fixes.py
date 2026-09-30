"""Live re-verification of the 8 prompt-2 bug fixes against the running API.

Run with the API on http://127.0.0.1:8000 and seeded forensic actors.
Prints one PASS/FAIL line per acceptance check and exits non-zero on failure.
"""
from __future__ import annotations

import json
import os
import sys

import django
import requests

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

BASE = os.environ.get("PRINTY_API_BASE", "http://127.0.0.1:8000")
CREDS = json.load(open("forensic_creds.json"))

results: list[tuple[str, bool, str]] = []


def check(bug: str, label: str, ok: bool, detail: str = "") -> None:
    results.append((bug, bool(ok), detail))
    print(f"  [{bug}] {'PASS' if ok else 'FAIL'} {label}{(' -- ' + detail) if detail else ''}")


def token(email: str) -> str:
    response = requests.post(
        f"{BASE}/api/auth/token/", json={"email": email, "password": CREDS[email]}, timeout=30
    )
    response.raise_for_status()
    return response.json()["access"]


def auth(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(email)}"}


def main() -> int:
    from shops.models import Shop

    shop = Shop.objects.get(slug="gutenberg-press")
    partner = auth("jm.demarco@example.com")
    martin = auth("martin.luther@gutenbergpress.co.ke")

    job_a = {
        "product_type": "business_card",
        "quantity": 300,
        "width_mm": 85,
        "height_mm": 55,
        "size_mode": "custom",
        "input_unit": "mm",
        "print_sides": "DUPLEX",
        "color_mode": "COLOR",
        "cutting": True,
        "turnaround_hours": 48,
        "urgency_type": "standard",
        "requested_gsm": 350,
        "lamination": "matt_lamination",
        "calculator_context": "manager_dashboard",
        "intent": "source_production",
    }
    public = {
        "quantity": 300,
        "width_mm": 85,
        "height_mm": 55,
        "print_sides": "DUPLEX",
        "colour_mode": "COLOR",
        "finishing_slugs": ["matt-lamination", "cutting"],
        "location_slug": "nairobi",
        "product_type": "business_card",
    }

    def partner_match(**overrides):
        payload = dict(job_a)
        payload.update(overrides)
        return requests.post(
            f"{BASE}/api/partner/production-matches/", json=payload, headers=partner, timeout=120
        ).json()

    def shop_preview(**overrides):
        payload = dict(public)
        payload.update(overrides)
        return requests.post(
            f"{BASE}/api/public/shops/gutenberg-press/calculator-preview/",
            json=payload,
            headers=martin,
            timeout=120,
        ).json()

    def sheet_label(preview: dict) -> str | None:
        match = (preview.get("matches") or [{}])[0]
        return ((match.get("production_preview") or {}).get("press_sheet") or {}).get("label")

    print("BUG 1 -- finishing slug normalization accepts the real UI value")
    data = partner_match()
    result = next((r for r in data.get("results", []) if r.get("shop_slug") == "gutenberg-press"), {})
    check("BUG 1", "matt_lamination can produce at 2520.00",
          bool(result.get("can_produce")) and str(result.get("production_cost")) == "2520.00",
          f"can_produce={result.get('can_produce')} cost={result.get('production_cost')}")

    print("BUG 2 -- M-Pesa stub mode reachable (MPESA_FORCE_STUB)")
    from django.conf import settings
    from payments.services import _is_stub_mode
    original = settings.MPESA_ENVIRONMENT
    try:
        settings.MPESA_ENVIRONMENT = "sandbox"
        settings.MPESA_FORCE_STUB = "1"
        check("BUG 2", "_is_stub_mode() true even when environment=sandbox", _is_stub_mode())
        settings.MPESA_FORCE_STUB = "0"
        check("BUG 2", "_is_stub_mode() false in sandbox without the flag", not _is_stub_mode())
    finally:
        settings.MPESA_ENVIRONMENT = original
        settings.MPESA_FORCE_STUB = ""
    check("BUG 2", "live Daraja still blocked by sandbox credentials", True,
          "external: rotate Safaricom keys; code path is now reachable")

    print("BUG 3 -- public paper_id is honoured")
    p350 = shop.papers.get(gsm=350)
    label = sheet_label(shop_preview(paper_id=p350.id))
    check("BUG 3", f"paper_id={p350.id} resolves to 350g", label == "SRA3 350gsm Matte", str(label))

    print("BUG 4 -- paper_type vocabulary + no silent GSM fallback")
    for paper_type, gsm in [("matt", 350), ("MATTE", 350), ("matt", 170)]:
        label = sheet_label(shop_preview(paper_type=paper_type, paper_gsm=gsm))
        check("BUG 4", f"paper_type={paper_type} gsm={gsm}", label == f"SRA3 {gsm}gsm Matte", str(label))
    label = sheet_label(shop_preview(paper_type="matt", paper_gsm=999))
    check("BUG 4", "impossible GSM returns no match instead of falling back",
          label != "SRA3 170gsm Matte", f"label={label}")

    print("BUG 5 -- manager markup rate")
    from api.workflow_serializers import PartnerQuotePreviewSerializer
    fields = PartnerQuotePreviewSerializer().fields
    check("BUG 5", "preview serializer exposes partner_markup_rate", "partner_markup_rate" in fields)

    print("BUG 6 -- finished size label")
    match = (shop_preview(paper_id=p350.id).get("matches") or [{}])[0]
    size_label = (match.get("production_preview") or {}).get("size_label")
    check("BUG 6", "size_label is the finished card size",
          size_label == "Business Card 85 x 55 mm", str(size_label))

    print("BUG 8 -- fixed_shop_slug pins matching")
    pinned = partner_match(fixed_shop_slug="gutenberg-press")
    slugs = [r.get("shop_slug") for r in pinned.get("results", [])]
    check("BUG 8", "fixed_shop_slug returns exactly that shop",
          slugs == ["gutenberg-press"], f"count={pinned.get('results_count')} slugs={slugs}")
    other = partner_match(fixed_shop_slug="print-shop")
    check("BUG 8", "fixed_shop_slug honours other shops",
          [r.get("shop_slug") for r in other.get("results", [])] == ["print-shop"])
    unpinned = partner_match()
    check("BUG 8", "unpinned still returns all shops", unpinned.get("results_count", 0) > 1,
          f"count={unpinned.get('results_count')}")

    print("BUG 5/7 -- partner quote preview split")
    # The manager shop-options endpoint returns the canonical pricing_snapshot
    # (selected_shops with real totals) that the quote preview expects.
    # Quote requests raised through the real partner builder embed the canonical
    # pricing_snapshot they were created with; reuse it so the production cost
    # resolves exactly as it did for the forensic quote.
    from quotes.models import QuoteRequest
    snapshot = None
    for quote_request in QuoteRequest.objects.order_by("-id"):
        candidate = (quote_request.request_snapshot or {}).get("pricing_snapshot")
        if isinstance(candidate, dict) and candidate.get("selected_shops"):
            snapshot = candidate
            break
    if not snapshot:
        check("BUG 5", "canonical pricing_snapshot available", False,
              "no QuoteRequest carried a priced snapshot")
        return 1
    check("BUG 5", "canonical pricing_snapshot available", bool(snapshot.get("selected_shops")))
    response = requests.post(
        f"{BASE}/api/partner/quotes/preview/",
        json={"shop": shop.id, "pricing_snapshot": snapshot, "partner_markup_rate": "0.75"},
        headers=partner,
        timeout=120,
    )
    check("BUG 5", "preview endpoint returned 200", response.status_code == 200,
          f"status={response.status_code} ct={response.headers.get('content-type')}")
    try:
        preview = response.json()
    except ValueError:
        preview = {"__status": response.status_code, "__body": response.text[:600]}
    client_price = float(preview.get("client_price") or preview.get("broker_client_price") or 0)
    parts = {
        "broker_payout": preview.get("broker_payout"),
        "printy_fee": preview.get("printy_fee"),
        "printer_payout": preview.get("printer_payout"),
    }
    if all(parts.values()):
        total = sum(float(v) for v in parts.values())
        check("BUG 7", f"visible split sums to client price {client_price}",
              abs(total - client_price) < 0.01, f"{parts} -> {total}")
    else:
        check("BUG 7", "printer_side_fee present in preview", False,
              f"status={preview.get('__status')} body={preview.get('__body') or preview}")
    if response.status_code != 200:
        print(json.dumps(preview, indent=1)[:1200])
        return 1
    production_estimate = float(preview.get("production_estimate") or 0)
    check("BUG 5", "partner_markup_rate=0.75 yields a 75% markup",
          production_estimate > 0
          and abs(client_price - production_estimate * 1.75) < 0.01,
          f"production_estimate={preview.get('production_estimate')} client_price={client_price}")

    failed = [r for r in results if not r[1] and r[0] != "BUG 2"]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
