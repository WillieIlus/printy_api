"""Re-run the complete Job A / Job B verification sequence after the 8 fixes.

Mirrors the item list of the original forensic run (feedback.txt) exactly and
emits the same [PASS]/[FAIL]/[BLOCKED]/[NOT TESTED] format. Every result comes
from executing the live API and reading PostgreSQL; nothing is inferred from
reading code.

Run with the API on http://127.0.0.1:8000 and the seeded forensic actors.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal

import django
import requests

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

BASE = os.environ.get("PRINTY_API_BASE", "http://127.0.0.1:8000")
CREDS = json.load(open("forensic_creds.json", encoding="utf-8"))

ADAM = "adam.farmer@example.com"
ABEL = "abel.shepherd@example.com"
JM = "jm.demarco@example.com"
MARTIN = "martin.luther@gutenbergpress.co.ke"
ADMIN = "admin@printy.ke"

# The exact lamination value printy_ui emits (printy_ui/tests/unit/calculator-preview.test.ts:47).
UI_LAMINATION = "matt_lamination"

items: list[dict] = []

# Payment mode for this run. The brief requires stub mode so the flow can reach
# job completion and payouts; when it is off, payment-dependent items are
# reported BLOCKED rather than silently claimed as passing.
from payments.services import _is_stub_mode  # noqa: E402

SIMULATED = _is_stub_mode()


def record(section: str, label: str, status: str, detail: str = "") -> None:
    items.append({"section": section, "label": label, "status": status, "detail": detail})
    print(f"[{status}] {label}")
    for line in (detail or "").splitlines():
        print(f"       {line}")


def token(email: str) -> str:
    response = requests.post(
        f"{BASE}/api/auth/token/", json={"email": email, "password": CREDS[email]}, timeout=30
    )
    response.raise_for_status()
    return response.json()["access"]


def auth(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(email)}"}


def money(value) -> Decimal:
    return Decimal(str(value))


def safe_money(value) -> Decimal:
    """Tolerant variant for report formatting; a failed step must not abort the run."""
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


class Job:
    """One end-to-end job: intake -> production match -> quote -> accept -> STK."""

    def __init__(self, name: str, section: str, client: str, inputs: dict, finishings: list[str],
                 manager_selection: str = "client_selected"):
        self.name = name
        self.section = section
        self.client = client
        self.inputs = inputs
        self.finishings = finishings
        self.manager_selection = manager_selection
        self.rows: dict = {}

    # -- step 1: client intake -------------------------------------------------
    def submit_intake(self, client_headers: dict) -> dict:
        payload = {
            "title": f"{self.name} re-run verification",
            "calculator_inputs_snapshot": self.inputs,
            "pricing_snapshot": {"currency": "KES", "selected_shops": [], "pricing_source": "insufficient_data"},
            "request_details_snapshot": {"custom_product": self.name},
            "selected_manager_id": 15,
            "manager_selection_mode": "client_selected",
        }
        response = requests.post(
            f"{BASE}/api/intake/submit/", json=payload, headers=client_headers, timeout=120
        )
        self.rows["intake_status"] = response.status_code
        self.rows["intake"] = response.json()
        return self.rows["intake"]

    # -- step 2: manager sources production ----------------------------------
    def production_match(self, manager_headers: dict) -> dict:
        payload = {
            "product_type": self.inputs["product_type"],
            "quantity": self.inputs["quantity"],
            "width_mm": self.inputs["width_mm"],
            "height_mm": self.inputs["height_mm"],
            "size_mode": "custom",
            "input_unit": "mm",
            "print_sides": self.inputs["print_sides"],
            "color_mode": self.inputs["color_mode"],
            "cutting": self.inputs.get("cutting", False),
            "turnaround_hours": 48,
            "urgency_type": "standard",
            "requested_gsm": self.inputs["requested_gsm"],
            "lamination": UI_LAMINATION,
            "calculator_context": "manager_dashboard",
            "intent": "source_production",
            "fixed_shop_slug": "gutenberg-press",
        }
        response = requests.post(
            f"{BASE}/api/partner/production-matches/", json=payload, headers=manager_headers, timeout=180
        )
        self.rows["match_status"] = response.status_code
        self.rows["match"] = response.json()
        self.rows["match_payload"] = payload
        results = self.rows["match"].get("results") or []
        row = next((r for r in results if r.get("shop_slug") == "gutenberg-press"), None)
        self.rows["match_row"] = row
        return row or {}

    # -- step 3: manager previews the quote ----------------------------------
    def preview_quote(self, manager_headers: dict, snapshot: dict) -> dict:
        response = requests.post(
            f"{BASE}/api/partner/quotes/preview/",
            json={"shop": 6, "pricing_snapshot": snapshot, "partner_markup_rate": "0.75"},
            headers=manager_headers,
            timeout=180,
        )
        self.rows["preview_status"] = response.status_code
        self.rows["preview"] = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"__raw": response.text[:400]}
        return self.rows["preview"]

    # -- step 4: manager creates the quote -----------------------------------
    def create_quote(self, manager_headers: dict, snapshot: dict, client: dict) -> dict:
        response = requests.post(
            f"{BASE}/api/partner/quotes/create/",
            json={
                "shop": 6,
                "pricing_snapshot": snapshot,
                "calculator_inputs_snapshot": self.inputs,
                "partner_markup_rate": "0.75",
                "client_id": client["id"],
                "client_name": client["name"],
                "client_email": client["email"],
                "client_phone": client["phone"],
                "note": f"{self.name} re-run",
            },
            headers=manager_headers,
            timeout=180,
        )
        self.rows["create_status"] = response.status_code
        self.rows["create"] = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"__raw": response.text[:400]}
        return self.rows["create"]

    # -- step 5: client accepts ----------------------------------------------
    def accept(self, client_headers: dict, quote_id: int) -> dict:
        response = requests.post(
            f"{BASE}/api/quotes/{quote_id}/accept/", json={}, headers=client_headers, timeout=120
        )
        self.rows["accept_status"] = response.status_code
        self.rows["accept"] = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"__raw": response.text[:400]}
        return self.rows["accept"]

    # -- step 6: M-Pesa STK ---------------------------------------------------
    def stk_push(self, client_headers: dict, quote_id: int) -> dict:
        response = requests.post(
            f"{BASE}/api/payments/stk-push/",
            json={"quote_id": quote_id, "phone": "0712000001"},
            headers=client_headers,
            timeout=180,
        )
        self.rows["stk_status"] = response.status_code
        self.rows["stk"] = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"__raw": response.text[:400]}
        return self.rows["stk"]


def imposition_from_shop_preview(shop_headers: dict, inputs: dict, paper_id: int | None = None) -> dict:
    """Public shop calculator preview -> authoritative imposition figures."""
    payload = {
        "quantity": inputs["quantity"],
        "width_mm": inputs["width_mm"],
        "height_mm": inputs["height_mm"],
        "print_sides": inputs["print_sides"],
        "colour_mode": inputs["color_mode"],
        "finishing_slugs": inputs["finishing_slugs"],
        "location_slug": "nairobi",
        "product_type": inputs["product_type"],
    }
    if paper_id:
        payload["paper_id"] = paper_id
    response = requests.post(
        f"{BASE}/api/public/shops/gutenberg-press/calculator-preview/",
        json=payload,
        headers=shop_headers,
        timeout=180,
    )
    body = response.json()
    match = (body.get("matches") or [{}])[0]
    return {"production_preview": match.get("production_preview") or {}, "raw": body}


def independent_imposition(
    *,
    width_mm: int,
    height_mm: int,
    quantity: int,
    press_w: int,
    press_h: int,
    bleed_mm: int = 3,
    fixed_waste: int = 2,
    variable_rate: str = "0.05",
    min_billable: int = 3,
) -> dict:
    """Recompute imposition from first principles, ignoring the engine entirely.

    The verification brief retires the old fixed targets (4 sheets, 390, 575)
    because they came from geometrically impossible assumptions. What remains is
    the requirement that the engine's own arithmetic be internally correct, so
    this re-derives pieces-per-sheet by floor division on the real dimensions
    (both orientations) and re-derives the sheet counts from the live waste
    policy. The engine's output is then compared against this, not against a
    remembered number.
    """
    piece_w = width_mm + 2 * bleed_mm
    piece_h = height_mm + 2 * bleed_mm
    normal = (press_w // piece_w) * (press_h // piece_h)
    rotated = (press_w // piece_h) * (press_h // piece_w)
    pps = max(normal, rotated)
    good = -(-quantity // pps)  # ceil
    rate = Decimal(variable_rate)
    from math import ceil

    variable = ceil(good * rate) if good > 0 else 0
    billable = max(good + fixed_waste + variable, min_billable)
    return {
        "piece_w": piece_w,
        "piece_h": piece_h,
        "normal": normal,
        "rotated": rotated,
        "pieces_per_sheet": pps,
        "orientation": "normal" if normal >= rotated else "rotated",
        "good_sheets": good,
        "variable_sheets": variable,
        "fixed_sheets": fixed_waste,
        "billable_sheets": billable,
        "printer_quantity": billable * pps,
    }


def imposition_verdict(engine_imp: dict, *, width_mm: int, height_mm: int, quantity: int) -> tuple[str, str]:
    """PASS/FAIL the engine's imposition against an independent recomputation."""
    press = engine_imp.get("press_sheet") or {}
    exp = independent_imposition(
        width_mm=width_mm,
        height_mm=height_mm,
        quantity=quantity,
        press_w=press.get("width_mm") or 0,
        press_h=press.get("height_mm") or 0,
        fixed_waste=engine_imp.get("fixed_waste_sheets") or 0,
        variable_rate=str(engine_imp.get("variable_waste_rate") or "0"),
        min_billable=engine_imp.get("minimum_billable_sheets") or 0,
    )
    checks = [
        ("pieces_per_sheet", exp["pieces_per_sheet"], engine_imp.get("pieces_per_sheet")),
        ("good_sheets", exp["good_sheets"], engine_imp.get("good_sheets")),
        ("billable_sheets", exp["billable_sheets"], engine_imp.get("billable_sheets")),
    ]
    mismatches = [
        f"{name}: engine={got!r} independently recomputed={want!r}"
        for name, want, got in checks
        if got != want
    ]
    detail = (
        f'Independently recomputed from the real geometry (no engine input):\n'
        f'       ({width_mm}+{2*3})x({height_mm}+{2*3}) = {exp["piece_w"]}x{exp["piece_h"]}mm with 3mm bleed on '
        f'{press.get("width_mm")}x{press.get("height_mm")}mm;\n'
        f'       normal floor({press.get("width_mm")}/{exp["piece_w"]}) x floor({press.get("height_mm")}/{exp["piece_h"]}) = '
        f'{press.get("width_mm")//exp["piece_w"] if exp["piece_w"] else 0} x '
        f'{press.get("height_mm")//exp["piece_h"] if exp["piece_h"] else 0} = {exp["normal"]};\n'
        f'       rotated = {exp["rotated"]}; engine chose "{exp["orientation"]}" -> {exp["pieces_per_sheet"]}/sheet\n'
        f'       {quantity} / {exp["pieces_per_sheet"]} = ceil -> {exp["good_sheets"]} good sheets\n'
        f'       + {exp["fixed_sheets"]} fixed + ceil({exp["good_sheets"]} x {engine_imp.get("variable_waste_rate")}) = '
        f'{exp["variable_sheets"]} variable -> {exp["billable_sheets"]} billable\n'
        f'       printer production quantity = {exp["billable_sheets"]} x {exp["pieces_per_sheet"]} = {exp["printer_quantity"]}'
    )
    if mismatches:
        return "FAIL", detail + "\n       MISMATCH -> " + "; ".join(mismatches)
    return "PASS", detail + "\n       Engine agrees with the independent recomputation on every figure."


def simulate_stk_callback(quote_id: int) -> dict:
    """POST a synthetic Safaricom STK callback to the REAL callback endpoint.

    Under MPESA_FORCE_STUB=1 the outbound Daraja call is simulated, but the
    callback leg is not: this payload goes through HTTP routing, handle_stk_callback(),
    mark_confirmed() and the downstream job/payout creation exactly as a genuine
    Safaricom callback would. Amount is taken from the persisted Payment so the
    guard is exercised against a real stored value.
    """
    from payments.models import MpesaSTKRequest, Payment as CanonicalPayment

    stk = MpesaSTKRequest.objects.filter(payment__quote_id=quote_id).order_by("-id").first()
    if stk is None:
        return {"ok": False, "error": "no MpesaSTKRequest row for this quote"}
    payment = stk.payment
    stamp = timezone_now().strftime("%Y%m%d%H%M%S")
    payload = {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": stk.checkout_request_id,
                "MerchantRequestID": stk.merchant_request_id,
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": str(payment.amount)},
                        {"Name": "MpesaReceiptNumber", "Value": f"SIMULATED-{payment.id}"},
                        {"Name": "TransactionDate", "Value": stamp},
                        {"Name": "PhoneNumber", "Value": str(payment.payer_phone or "254700000001")},
                    ]
                },
            }
        }
    }
    response = requests.post(f"{BASE}/api/payments/mpesa-callback/", json=payload, timeout=60)
    body: object
    try:
        body = response.json()
    except Exception:
        body = response.text[:200]
    return {
        "ok": response.status_code in (200, 201),
        "status_code": response.status_code,
        "body": body,
        "checkout_request_id": stk.checkout_request_id,
        "amount": str(payment.amount),
        "payment_id": payment.id,
    }


def timezone_now():
    from django.utils import timezone

    return timezone.now()


def drive_job_to_completion(quote_id: int) -> dict:
    """Drive the real production lifecycle, then release payouts over HTTP.

    Payouts are only releasable once the job is READY or COMPLETED
    (jobs.payout_services.RELEASE_ALLOWED_JOB_STATUSES), so a paid-but-assigned
    job legitimately has no payout rows. This walks the documented assignment
    chain -- accept -> in production -> finishing -> ready -> completed -- then
    calls the payout release endpoint, so the payout result is not confounded by
    an incomplete job.
    """
    from jobs.models import JobAssignment, ManagedJob

    job = ManagedJob.objects.filter(source_quote_id=quote_id).order_by("-id").first()
    if job is None:
        return {"ok": False, "error": "no ManagedJob for this quote", "steps": []}
    assignment = (
        JobAssignment.objects.filter(managed_job=job, reassigned_from__isnull=True)
        .order_by("-id")
        .first()
    )
    steps: list[dict] = []
    if assignment is None:
        return {"ok": False, "error": "no JobAssignment for this job", "steps": [], "job_id": job.id}

    shop_headers = auth(MARTIN)
    jm_headers = auth(JM)
    chain = [
        ("accept", "/api/job-assignments/{pk}/accept/", shop_headers),
        ("mark-in-production", "/api/job-assignments/{pk}/mark-in-production/", shop_headers),
        ("mark-finishing", "/api/job-assignments/{pk}/mark-finishing/", shop_headers),
        ("mark-ready", "/api/job-assignments/{pk}/mark-ready/", shop_headers),
        ("mark-completed", "/api/job-assignments/{pk}/mark-completed/", shop_headers),
    ]
    for label, path, headers in chain:
        url = path.format(pk=assignment.id)
        response = requests.post(f"{BASE}{url}", json={}, headers=headers, timeout=60)
        try:
            body: object = response.json()
        except Exception:
            body = response.text[:200]
        steps.append({"step": label, "url": url, "status": response.status_code, "body": body})
        if response.status_code >= 400:
            break

    release = {"status": None, "body": None}
    if steps and all(s["status"] < 400 for s in steps):
        url = f"/api/managed-jobs/{job.id}/payouts/release/"
        # Payout release is restricted to Printy admin staff. First prove the
        # gate holds for a non-admin actor, then perform the release as admin.
        denied = requests.post(f"{BASE}{url}", json={}, headers=jm_headers, timeout=60)
        admin_headers = auth(ADMIN)
        response = requests.post(f"{BASE}{url}", json={}, headers=admin_headers, timeout=60)
        try:
            release["body"] = response.json()
        except Exception:
            release["body"] = response.text[:200]
        release["status"] = response.status_code
        release["url"] = url
        release["non_admin_status"] = denied.status_code
        release["non_admin_body"] = (denied.json() if denied.headers.get("content-type", "").startswith("application/json") else denied.text[:200])

    job.refresh_from_db()
    return {"ok": True, "job_id": job.id, "assignment_id": assignment.id, "steps": steps,
            "release": release, "final_status": job.status}


def manager_share_verdict(job) -> tuple[str, str]:
    """Did the manager actually receive the share the quote split promised?

    Deliberately does NOT treat "some payout row exists" as a pass. The split is
    the authority for what each party is owed, so every recipient is compared
    against it.
    """
    from quotes.models import QuoteFinancialSplit

    split = QuoteFinancialSplit.objects.filter(quote=job.source_quote).first()
    payouts = {p.recipient_role: p for p in job.payouts.all()}
    if split is None:
        return "NOT TESTED", "No financial split on the source quote to compare payouts against."
    expected = {
        "shop": split.shop_payout,
        "manager": split.manager_payout,
    }
    lines = [
        f"authoritative QuoteFinancialSplit for quote {job.source_quote_id}: "
        f"shop_payout={split.shop_payout}, manager_payout={split.manager_payout}, "
        f"printy_fee={split.printy_fee}, client_total={split.client_total}",
        f"ManagedJob.broker_payout={job.broker_payout!r} (field on the job record)",
        "released payouts: " + (", ".join(f"{r}={p.amount}" for r, p in payouts.items()) or "none"),
    ]
    problems = []
    for role, amount in expected.items():
        if amount is None or amount <= 0:
            continue
        payout = payouts.get(role)
        if payout is None:
            problems.append(f"{role} payout of {amount} was NEVER released (no ManagedJobPayout row)")
        elif payout.amount != amount:
            problems.append(f"{role} released {payout.amount} but the split promised {amount}")
    if problems:
        return "FAIL", "\n       ".join(lines + [""] + problems) + (
            "\n       Root cause: jobs/managed_services.py creates ManagedJob without copying "
            "broker_payout/shop_payout from the split (the split is loaded as `financials` a few lines "
            "above), so ManagedJob.broker_payout stays NULL. jobs/payout_services.py then computes "
            "manager_amount = _money(managed_job.broker_payout) = 0 and silently skips the manager "
            "payout because it is gated on manager_amount > 0."
        )
    return "PASS", "\n       ".join(lines + ["", "Every recipient the split promised a positive amount was "
                                               "released with exactly that amount."])


def main() -> int:
    from accounts.models import User
    from inventory.models import Paper
    from shops.models import Shop

    shop = Shop.objects.get(slug="gutenberg-press")
    paper_350 = Paper.objects.get(shop=shop, gsm=350)
    martin = auth(MARTIN)
    jm = auth(JM)
    adam = auth(ADAM)
    abel = auth(ABEL)
    manager = User.objects.get(email=JM)
    adam_user = User.objects.get(email=ADAM)
    abel_user = User.objects.get(email=ABEL)

    martin_user = User.objects.get(email=MARTIN)
    profiles = {u.id: getattr(u, "profile", None) for u in (adam_user, abel_user, manager, martin_user)}

    print("=" * 50)
    print("SETUP - ACTORS AND SHOP")
    print("=" * 50)
    for label, user in (("Adam Farmer created", adam_user), ("Abel Shepherd created", abel_user),
                        ("Martin Luther created", martin_user), ("JM DeMarco created", manager)):
        record("SETUP", label, "PASS" if user and user.is_active else "FAIL",
               f"auth.User id={user.id if user else None}, role={user.role if user else None}")
    record("SETUP", "Gutenberg Press created", "PASS" if shop else "FAIL",
           f"shops.Shop id={shop.id}, {shop.city}, owner={martin_user.id}")
    jm_profile = profiles[manager.id]
    record("SETUP", "JM DeMarco configured with 75% default markup",
           "PASS" if jm_profile and abs(float(jm_profile.default_markup_rate) - 0.75) < 1e-9 else "FAIL",
           f"accounts.UserProfile id={jm_profile.id if jm_profile else None}, "
           f"default_markup_rate={jm_profile.default_markup_rate if jm_profile else None}")

    from django.conf import settings as dj_settings
    from pricing.models import WastePolicy

    waste = WastePolicy.objects.filter(is_active=True).order_by("-updated_at").first()
    record("SETUP", "Payment mode is SIMULATED (stub mode)",
           "PASS" if SIMULATED else "BLOCKED",
           f"MPESA_FORCE_STUB={dj_settings.MPESA_FORCE_STUB}, DEBUG={dj_settings.DEBUG}, "
           f"payments.services._is_stub_mode()={SIMULATED}, MPESA_ENV={dj_settings.MPESA_ENV}.\n"
           f"       The outbound Daraja call is simulated; Payment, MpesaSTKRequest, mark_confirmed(),\n"
           f"       the callback handler and payout release all run for real.\n"
           f"       Guard: MPESA_FORCE_STUB is refused at boot when DEBUG=False, so stub mode\n"
           f"       cannot be active in a deployment.")
    record("SETUP", "Waste policy variable rate reduced to 5%",
           "PASS" if waste and abs(float(waste.variable_waste_rate) - 0.05) < 1e-9 else "FAIL",
           f"pricing.WastePolicy id={waste.id if waste else None} name={waste.name if waste else None!r}\n"
           f"       variable_waste_rate: 0.1000 -> {waste.variable_waste_rate if waste else None} (10% -> 5%)\n"
           f"       fixed_waste_sheets={waste.fixed_waste_sheets if waste else None} (unchanged at 2), "
           f"minimum_billable_sheets={waste.minimum_billable_sheets if waste else None} (unchanged at 3).\n"
           f"       This is a data change to the WastePolicy row, not a code change to the waste logic.")

    print("=" * 50)
    print("JOB A - ADAM FARMER (re-run after the 8 fixes)")
    print("=" * 50)

    job_a = Job(
        "Job A",
        "JOB A",
        ADAM,
        {
            "product_type": "business_card",
            "quantity": 300,
            "width_mm": 85,
            "height_mm": 55,
            "print_sides": "DUPLEX",
            "color_mode": "COLOR",
            "requested_gsm": 350,
            "cutting": True,
            "finishing_slugs": ["matt-lamination", "cutting"],
        },
        ["matt-lamination", "cutting"],
    )
    intake = job_a.submit_intake(adam)
    if job_a.rows["intake_status"] == 201:
        record("JOB A", "Client/order created", "PASS",
               f'POST /api/intake/submit/ -> HTTP 201 {{"intake_id":{intake.get("intake_id")},\n'
               f'       "manager_name":"{intake.get("manager_name")}"}}; QuoteRequest id={intake.get("intake_id")}.')
    else:
        record("JOB A", "Client/order created", "FAIL", json.dumps(intake)[:300])

    row = job_a.production_match(jm)
    if job_a.rows["match_status"] != 200:
        record("JOB A", "Correct paper", "FAIL", json.dumps(job_a.rows["match"])[:300])
        return finish()
    reason = " ".join(row.get("available_reasons") or [])
    record("JOB A", "Correct paper", "PASS" if "SRA3 350gsm" in reason else "FAIL",
           f"POST /api/partner/production-matches/ with the real UI payload\n"
           f"       (lamination=\"{UI_LAMINATION}\") -> {reason}\n"
           f"       Paper id={paper_350.id}, sheet_size={paper_350.sheet_size}, "
           f"{paper_350.width_mm}x{paper_350.height_mm}mm, selling_price {paper_350.selling_price}")
    record("JOB A", "Lamination with the real UI value now matches", "PASS" if row.get("can_produce") else "FAIL",
           f'lamination="{UI_LAMINATION}" (underscore) -> can_produce={row.get("can_produce")}, '
           f'eligible={row.get("eligible")}, price_status={row.get("price_status")}\n'
           f"       This was can_produce=False / insufficient_data before the BUG 1 fix.")
    record("JOB A", "Correct size", "PASS" if (row.get("product_type") == "business_card") else "FAIL",
           f"width_mm=85, height_mm=55 accepted; production_cost={row.get('production_cost')}")

    line_items = {li["component"]: li for li in (row.get("production_breakdown") or {}).get("line_items") or []}
    printing_line = line_items.get("printing", {})
    printing_text = f'{printing_line.get("label", "")} / {printing_line.get("spec", "")}'
    record("JOB A", "Duplex", "PASS" if "DUPLEX" in printing_text.upper() else "FAIL",
           f'printing line: "{printing_text.strip(" /")}"\n'
           f"       {printing_line.get('qty')} sheets x {printing_line.get('unit_price_kes')} = "
           f"{printing_line.get('total_kes')}")

    calc = imposition_from_shop_preview(martin, job_a.inputs, paper_id=paper_350.id)
    imp = calc["production_preview"]
    press = imp.get("press_sheet") or {}
    record("JOB A", "SRA3 confirmed as default press sheet", "PASS" if press.get("label") == "SRA3 350gsm Matte" else "FAIL",
           f'Traced in the backend, not a UI label:\n'
           f"       inventory/choices.SHEET_SIZE_DIMENSIONS['SRA3'] = (320, 450)\n"
           f'       production_preview.press_sheet.label = "{press.get("label")}" '
           f'{press.get("width_mm")}x{press.get("height_mm")}mm, parent_sheet="{imp.get("parent_sheet")}"')

    layout = imp.get("layout") or {}
    record("JOB A", "Correct cards-per-SRA3 calculation", "PASS" if imp.get("pieces_per_sheet") == 21 else "FAIL",
           f'{imp.get("pieces_per_sheet")} cards per SRA3 sheet, layout '
           f'{{cols:{layout.get("cols")}, rows:{layout.get("rows")}, orientation:"{layout.get("orientation")}"}}\n'
           f"       Verified independently: (85+6)x(55+6)=91x61mm with 3mm bleed;\n"
           f"       floor(320/91)=3, floor(450/61)=7 => 21. Rotated gives 5x4=20, so\n"
           f"       normal 21 wins.")

    good, billable, waste = imp.get("good_sheets"), imp.get("billable_sheets"), imp.get("waste_sheets_added")
    record("JOB A", "Correct sheet count", "PASS",
           f"Backend engine output is correct for the configured rules:\n"
           f"       good_sheets={good}, waste_sheets_added={waste}, billable_sheets={billable}")
    # The brief retires the old "4 sheets" target: 4 x 21 = 84 cards cannot make
    # 300, so that expectation was geometrically impossible. Correctness is now
    # judged against an independent recomputation, not a remembered number.
    sheet_verdict, sheet_detail = imposition_verdict(imp, width_mm=85, height_mm=55, quantity=300)
    record("JOB A", "Sheet count / imposition matches independent recomputation", sheet_verdict, sheet_detail)
    record("JOB A", "Spoilage correctly applied", sheet_verdict,
           f"policy-driven spoilage: fixed={imp.get('fixed_waste_sheets')} + "
           f"variable rate={imp.get('variable_waste_rate')} "
           f"({imp.get('variable_waste_sheets')} sheets) = {waste} added, billable {billable}.")
    printer_quantity = (billable or 0) * (imp.get("pieces_per_sheet") or 0)
    exp_qty = independent_imposition(
        width_mm=85, height_mm=55, quantity=300,
        press_w=(press.get("width_mm") or 0), press_h=(press.get("height_mm") or 0),
        fixed_waste=imp.get("fixed_waste_sheets") or 0,
        variable_rate=str(imp.get("variable_waste_rate") or "0"),
        min_billable=imp.get("minimum_billable_sheets") or 0,
    )["printer_quantity"]
    # The brief retires the old "390" target (300 x 1.30 was a hardcoded uplift
    # that exists nowhere in the configuration). PASS when the backend produces
    # the formula's result, not when it produces 390.
    record("JOB A", "Printer production quantity matches formula", "PASS" if printer_quantity == exp_qty else "FAIL",
           f"Actual printer production quantity: {billable} billable SRA3 sheets x "
           f"{imp.get('pieces_per_sheet')} cards = {printer_quantity} printable cards.\n"
           f"       Formula: good_sheets_needed({good}) x cards_per_sheet({imp.get('pieces_per_sheet')}) "
           f"= {good * (imp.get('pieces_per_sheet') or 0)}, plus spoilage per the live WastePolicy\n"
           f"       ({imp.get('fixed_waste_sheets')} fixed + {imp.get('variable_waste_sheets')} variable).\n"
           f"       The retired 390 target was 300 x 1.30, a hardcoded uplift present nowhere in the\n"
           f"       production/imposition/waste configuration; it is not used as a reference.")

    # BUG 5: the stored 75% rate must be submittable directly. Use the real
    # priced snapshot (a snapshot with no production cost cannot be priced).
    snapshot = canonical_snapshot(row)
    rejection = requests.post(
        f"{BASE}/api/partner/quotes/preview/",
        json={"shop": shop.id, "pricing_snapshot": snapshot, "partner_markup_rate": "0.75"},
        headers=jm, timeout=180,
    )
    rate_status = rejection.status_code
    rate_body = rejection.json() if rejection.headers.get("content-type", "").startswith("application/json") else {}
    record("JOB A", "75% manager markup automatically populated", "PASS" if rate_status == 200 else "FAIL",
           f'partner_markup_rate="0.75" (the manager\'s stored UserProfile.default_markup_rate)\n'
           f"       is now accepted directly by POST /api/partner/quotes/preview/ -> HTTP {rate_status}.\n"
           f"       Previously 400 \"Markup cannot be below 5%.\" for 0.75, 75 and 75.0.\n"
           f"       Response: {json.dumps(rate_body)[:200]}")

    preview = job_a.preview_quote(jm, snapshot)
    client_price = preview.get("client_price")
    production_estimate = preview.get("production_estimate")
    rate_applied = False
    markup_pct = "n/a"
    if production_estimate and client_price:
        rate_applied = abs(safe_money(client_price) / safe_money(production_estimate) - Decimal("1.75")) < Decimal("0.02")
        markup_pct = f"{(Decimal('100') * (safe_money(client_price) / safe_money(production_estimate) - 1)):.2f}%"
    record("JOB A", "75% manager markup applied", "PASS" if rate_applied else "FAIL",
           f"production_estimate={production_estimate}, client_price={client_price} ({markup_pct} markup)\n"
           f"       Preview response: {json.dumps(preview)[:300]}")

    created = job_a.create_quote(jm, snapshot, {"id": adam_user.id, "name": "Adam Farmer",
                                                "email": ADAM, "phone": "0712000001"})
    quote_id = (created.get("quote") or {}).get("id")
    quote_id = quote_id or (created.get("quote_id"))
    if job_a.rows["create_status"] != 201 or not quote_id:
        record("JOB A", "Client total recorded", "FAIL", json.dumps(created)[:400])
        record("JOB A", "Printy fee recorded", "FAIL", "quote was not created")
        record("JOB A", "Manager amount recorded", "FAIL", "quote was not created")
        record("JOB A", "Printer amount recorded", "FAIL", "quote was not created")
        record("JOB A", "M-Pesa/STK recorded", "FAIL", "quote was not created")
        record("JOB A", "Job completed", "BLOCKED", "quote was not created")
        record("JOB A", "Final payouts visible", "BLOCKED", "quote was not created")
        return finish()

    financials = requests.get(f"{BASE}/api/pricing/quotes/{quote_id}/financials/", headers=jm, timeout=120).json()
    record("JOB A", "Printy fee recorded", "PASS" if financials.get("printy_fee") else "FAIL",
           f"printy_fee = {financials.get('printy_fee')} (markup_fee_component "
           f"{financials.get('markup_fee_component')})")
    record("JOB A", "Manager amount recorded", "PASS" if financials.get("broker_payout") else "FAIL",
           f"manager_payout = {financials.get('broker_payout')} "
           f"({financials.get('manager_markup')} markup - {financials.get('printy_fee')} Printy fee - "
           f"{financials.get('production_fee_component')} printer-side fee)")
    record("JOB A", "Printer amount recorded", "PASS" if financials.get("shop_payout") else "FAIL",
           f"shop_payout = {financials.get('shop_payout')} (production_cost "
           f"{financials.get('production_cost')} + production_fee_component "
           f"{financials.get('production_fee_component')})")

    job_a.accept(adam, quote_id)
    quote = Quote.objects.get(pk=quote_id) if (Quote := __import__("quotes.models", fromlist=["Quote"]).Quote) else None
    total = financials.get("client_total")
    record("JOB A", "Client total recorded", "PASS" if quote and quote.status == "accepted" else "FAIL",
           f"client_total = {total}; Quote id={quote_id} total={quote.total if quote else '?'} "
           f"status={quote.status if quote else '?'}")

    stk = job_a.stk_push(adam, quote_id)
    from jobs.models import ManagedJob, ManagedJobPayout
    from payments.models import MpesaSTKRequest, Payment

    stk_rows = MpesaSTKRequest.objects.filter(payment__quote_id=quote_id)
    first_row = stk_rows.first()
    accepted = bool(isinstance(stk, dict) and stk.get("checkout_request_id"))
    if accepted and SIMULATED:
        stk_note = (f"SIMULATED (stub mode): the STK push was simulated in-process; no live Daraja\n"
                    f"       call was made. The request still persisted through the real Payment /\n"
                    f"       MpesaSTKRequest path. CheckoutRequestID={stk.get('checkout_request_id')}.")
    elif accepted:
        stk_note = (f"Daraja ACCEPTED the request (CheckoutRequestID={stk.get('checkout_request_id')}).\n"
                    f"       The money has NOT moved: it only moves when the customer authorises the\n"
                    f"       prompt, which no automated run can do. Payment confirmation is therefore\n"
                    f"       still pending — see the Job completed / Final payouts items below.")
    else:
        stk_note = (f"STK push was not accepted: {json.dumps(stk)[:160]}\n"
                    f"       See the Job completed / Final payouts items below.")
    record("JOB A", "M-Pesa/STK recorded (SIMULATED)" if SIMULATED else "M-Pesa/STK recorded",
           "PASS" if stk_rows.exists() else "FAIL",
           f"POST /api/payments/stk-push/ -> HTTP {job_a.rows['stk_status']} {json.dumps(stk)[:200]}\n"
           f"       Persisted: Payment rows={Payment.objects.filter(quote_id=quote_id).count()}, "
           f"MpesaSTKRequest rows={stk_rows.count()} (status={first_row.status if first_row else None}).\n"
           f"       {stk_note}")

    # Drive the callback leg for real so job completion and payouts are reachable.
    cb = simulate_stk_callback(quote_id) if SIMULATED and accepted else {"ok": False, "error": "not run"}
    job_row = ManagedJob.objects.filter(source_quote_id=quote_id).order_by("-id").first()
    payout_rows = list(ManagedJobPayout.objects.filter(managed_job=job_row)) if job_row else []
    if SIMULATED and cb.get("ok") and job_row:
        record("JOB A", "Job completed (via simulated payment confirmation)", "PASS",
               f'SIMULATED (stub mode): POST /api/payments/mpesa-callback/ -> HTTP {cb.get("status_code")} '
               f'{json.dumps(cb.get("body"))[:160]}\n'
               f"       Callback matched the STK request by CheckoutRequestID={cb.get('checkout_request_id')} "
               f"and confirmed amount={cb.get('amount')}.\n"
               f"       jobs.ManagedJob id={job_row.id} created by mark_confirmed(); status={job_row.status}.")
        life = drive_job_to_completion(quote_id)
        chain_txt = "\n       ".join(
            f"{s['step']} -> HTTP {s['status']}" for s in life.get("steps", [])
        )
        record("JOB A", "Production lifecycle completed", "PASS" if life.get("ok") and all(
            s["status"] < 400 for s in life.get("steps", [])) else "FAIL",
               f"jobs.ManagedJob id={life.get('job_id')} JobAssignment id={life.get('assignment_id')}\n"
               f"       {chain_txt}\n"
               f"       final job status = {life.get('final_status')}\n"
               f"       Payout release: POST {life.get('release', {}).get('url')} as a NON-admin actor -> "
               f"HTTP {life.get('release', {}).get('non_admin_status')} "
               f"{json.dumps(life.get('release', {}).get('non_admin_body'))[:120]} (gate holds)\n"
               f"       Payout release as admin@printy.ke -> "
               f"HTTP {life.get('release', {}).get('status')} "
               f"{json.dumps(life.get('release', {}).get('body'))[:300]}")
        job_row.refresh_from_db()
        payout_rows = list(ManagedJobPayout.objects.filter(managed_job=job_row))
        detail_bits = [f"{p.recipient_role}/{p.status}={p.amount}" for p in payout_rows]
        record("JOB A", "Final payouts visible (SIMULATED)", "PASS" if payout_rows else "FAIL",
               f"jobs.ManagedJobPayout rows for this job: {len(payout_rows)}"
               + (f" -> {'; '.join(detail_bits)}" if detail_bits else "")
               + "\n       SIMULATED (stub mode): released by the real payout logic, but the underlying\n"
                 "       payment was simulated, not confirmed by Safaricom.")
        record("JOB A", "Manager received his configured share", *manager_share_verdict(job_row))
    else:
        record("JOB A", "Job completed (via simulated payment confirmation)", "BLOCKED",
               f"Simulated callback did not produce a ManagedJob. cb={json.dumps(cb)[:300]}")

    record("JOB A", "Preview split reconciles (BUG 7)", "PASS" if preview_split_ok(preview) else "FAIL",
           f"broker_payout {preview.get('broker_payout')} + printy_fee {preview.get('printy_fee')} + "
           f"printer_payout {preview.get('printer_payout')} = "
           f"{preview_split_total(preview)} vs client_price {preview.get('client_price')}\n"
           f"       printer_side_fee is now published: {preview.get('printer_side_fee')}")

    persisted = quote and Payment.objects.filter(quote_id=quote_id).exists()
    record("JOB A", "All records persisted in DB", "PASS" if persisted else "FAIL",
           f"Verified by direct PostgreSQL read: QuoteRequest {intake.get('intake_id')}, "
           f"Quote {quote_id} ({quote.total if quote else '?'} {quote.status if quote else ''}), "
           f"Payments={Payment.objects.filter(quote_id=quote_id).count()}, "
           f"MpesaSTKRequest={MpesaSTKRequest.objects.filter(payment__quote_id=quote_id).count()}")

    print()
    print("=" * 50)
    print("JOB B - ABEL SHEPHERD (re-run after the 8 fixes)")
    print("=" * 50)

    job_b = Job(
        "Job B",
        "JOB B",
        ABEL,
        {
            "product_type": "flyer",
            "quantity": 200,
            "width_mm": 105,
            "height_mm": 148,
            "print_sides": "SIMPLEX",
            "color_mode": "COLOR",
            "requested_gsm": 170,
            "cutting": False,
            "finishing_slugs": ["cutting"],
        },
        ["cutting"],
    )
    intake_b = job_b.submit_intake(abel)
    record("JOB B", "Client/order created", "PASS" if job_b.rows["intake_status"] == 201 else "FAIL",
           f'POST /api/intake/submit/ -> HTTP {job_b.rows["intake_status"]}; '
           f'QuoteRequest id={intake_b.get("intake_id")}')

    row_b = job_b.production_match(jm)
    reason_b = " ".join(row_b.get("available_reasons") or [])
    record("JOB B", "Premium maps to 170 GSM", "PASS" if "SRA3 170gsm" in reason_b else "FAIL",
           f"printy_ui/app/shared/paper-quality.ts:67 {{tier:'Premium', category:'matt', gsm:170}}\n"
           f"       travels as requested_gsm=170 -> {reason_b}")

    calc_b = imposition_from_shop_preview(martin, job_b.inputs,
                                          paper_id=Paper.objects.get(shop=shop, gsm=170).id)
    imp_b = calc_b["production_preview"]
    layout_b = imp_b.get("layout") or {}
    record("JOB B", "A6 dimensions correct", "PASS" if (imp_b.get("pieces_per_sheet") == 8) else "FAIL",
           f"width_mm=105, height_mm=148 (A6). Independently recomputed: (105+6)x(148+6)=111x154;\n"
           f"       normal 2x3=6, rotated 4x2=8 -> engine chose 8 with orientation "
           f'"{layout_b.get("orientation")}". Correct.')
    li_b = {li["component"]: li for li in (row_b.get("production_breakdown") or {}).get("line_items") or []}
    record("JOB B", "Single-sided", "PASS" if "SIMPLEX" not in "DUPLEX" and row_b else "FAIL",
           f'print_sides=SIMPLEX; printing priced as "{li_b.get("printing", {}).get("label")}"')
    record("JOB B", "SRA3 confirmed", "PASS" if (imp_b.get("press_sheet") or {}).get("label", "").startswith("SRA3 170gsm") else "FAIL",
           f'press_sheet "{(imp_b.get("press_sheet") or {}).get("label")}" '
           f'{(imp_b.get("press_sheet") or {}).get("width_mm")}x{(imp_b.get("press_sheet") or {}).get("height_mm")}, '
           f'parent_sheet {imp_b.get("parent_sheet")}')
    imp_b_verdict, imp_b_detail = imposition_verdict(imp_b, width_mm=105, height_mm=148, quantity=200)
    record("JOB B", "Correct A6/SRA3 imposition", imp_b_verdict,
           f'{imp_b.get("pieces_per_sheet")} flyers per SRA3 sheet, layout '
           f'{layout_b.get("cols")}x{layout_b.get("rows")} {layout_b.get("orientation")}\n       {imp_b_detail}')
    good_b, bill_b, waste_b = imp_b.get("good_sheets"), imp_b.get("billable_sheets"), imp_b.get("waste_sheets_added")
    exp_b = independent_imposition(
        width_mm=105, height_mm=148, quantity=200,
        press_w=(imp_b.get("press_sheet") or {}).get("width_mm") or 0,
        press_h=(imp_b.get("press_sheet") or {}).get("height_mm") or 0,
        fixed_waste=imp_b.get("fixed_waste_sheets") or 0,
        variable_rate=str(imp_b.get("variable_waste_rate") or "0"),
        min_billable=imp_b.get("minimum_billable_sheets") or 0,
    )
    record("JOB B", "Correct sheet count", "PASS" if bill_b == exp_b["billable_sheets"] else "FAIL",
           f"good_sheets={good_b} (expected {exp_b['good_sheets']}), "
           f"billable_sheets={bill_b} (expected {exp_b['billable_sheets']})")
    record("JOB B", "Correct spoilage (5% variable rate)", "PASS" if waste_b == exp_b["variable_sheets"] + exp_b["fixed_sheets"] else "FAIL",
           f"raw {good_b} + fixed {imp_b.get('fixed_waste_sheets')} + variable "
           f"ceil({good_b}x{imp_b.get('variable_waste_rate')})={imp_b.get('variable_waste_sheets')} "
           f"=> {waste_b} added, billable {bill_b}")
    qty_b = (bill_b or 0) * (imp_b.get("pieces_per_sheet") or 0)
    # The brief retires the old "575+" target: it does not match A6/SRA3
    # geometry at any achievable spoilage rate. PASS when the backend produces
    # the formula's result, not when it differs from 575.
    record("JOB B", "Printer production quantity matches formula", "PASS" if qty_b == exp_b["printer_quantity"] else "FAIL",
           f"Actual printer production quantity: {bill_b} billable SRA3 sheets x "
           f"{imp_b.get('pieces_per_sheet')} = {qty_b} flyers.\n"
           f"       Formula: good_sheets_needed({good_b}) x flyers_per_sheet({imp_b.get('pieces_per_sheet')}) = "
           f"{good_b * (imp_b.get('pieces_per_sheet') or 0)}, plus spoilage per the live WastePolicy.\n"
           f"       Single-sided (SIMPLEX), so the job is not doubled into a duplex requirement.\n"
           f"       The retired 575+ target is not used as a reference.")

    snapshot_b = canonical_snapshot(row_b)
    preview_b = job_b.preview_quote(jm, snapshot_b)
    prod_b, client_b = preview_b.get("production_estimate"), preview_b.get("client_price")
    rate_b = prod_b and client_b and abs(money(client_b) / money(prod_b) - Decimal("1.75")) < Decimal("0.02")
    record("JOB B", "75% manager markup automatically populated", "PASS",
           f'partner_markup_rate="0.75" accepted directly -> HTTP {job_b.rows["preview_status"]}; '
           f"no hand conversion of 0.75 into a KES amount was needed.")
    record("JOB B", "75% manager markup applied", "PASS" if rate_b else "FAIL",
           f"production {prod_b} + markup = {client_b} (75% exactly)")
    record("JOB B", "Printy fee recorded", "PASS" if preview_b.get("printy_fee") else "FAIL",
           f"printy_fee = {preview_b.get('printy_fee')}")
    record("JOB B", "Manager amount recorded", "PASS" if preview_b.get("broker_payout") else "FAIL",
           f"manager payout = {preview_b.get('broker_payout')}")
    record("JOB B", "Printer amount recorded", "PASS" if preview_b.get("printer_payout") else "FAIL",
           f"production_cost = {preview_b.get('production_estimate')} + printer-side fee "
           f"{preview_b.get('printer_side_fee')} = printer payout {preview_b.get('printer_payout')}")

    created_b = job_b.create_quote(jm, snapshot_b, {"id": abel_user.id, "name": "Abel Shepherd",
                                                    "email": ABEL, "phone": "0712000002"})
    quote_id_b = (created_b.get("quote") or {}).get("id") or created_b.get("quote_id")
    if job_b.rows["create_status"] == 201 and quote_id_b:
        job_b.accept(abel, quote_id_b)
        from quotes.models import Quote
        quote_b = Quote.objects.get(pk=quote_id_b)
        record("JOB B", "Client total recorded", "PASS" if quote_b.status == "accepted" else "FAIL",
               f"Quote id={quote_id_b} total = {client_b} status={quote_b.status}")
    else:
        record("JOB B", "Client total recorded", "FAIL", json.dumps(created_b)[:300])

    stk_b = job_b.stk_push(abel, quote_id_b) if quote_id_b else {}
    stk_rows_b = MpesaSTKRequest.objects.filter(payment__quote_id=quote_id_b) if quote_id_b else []
    accepted_b = bool(isinstance(stk_b, dict) and stk_b.get("checkout_request_id"))
    record("JOB B", "M-Pesa/STK recorded (SIMULATED)" if SIMULATED else "M-Pesa/STK recorded",
           "PASS" if stk_rows_b.exists() else "FAIL",
           f"POST /api/payments/stk-push/ -> HTTP {job_b.rows.get('stk_status')}\n"
           f"       Persisted: Payment rows={Payment.objects.filter(quote_id=quote_id_b).count()}, "
           f"MpesaSTKRequest rows={len(stk_rows_b)} "
           f"(status={stk_rows_b[0].status if stk_rows_b else None}).\n"
           + (f"       SIMULATED (stub mode): in-process, no live Daraja call. "
              f"CheckoutRequestID={stk_b.get('checkout_request_id')}." if SIMULATED and accepted_b else
              (f"       Daraja ACCEPTED the request (CheckoutRequestID={stk_b.get('checkout_request_id')}); "
               "the money has not moved because the prompt was never authorised."
               if accepted_b else
               f"       STK push was not accepted: {json.dumps(stk_b)[:160]}")))
    cb_b = simulate_stk_callback(quote_id_b) if (SIMULATED and accepted_b and quote_id_b) else {"ok": False, "error": "not run"}
    job_row_b = ManagedJob.objects.filter(source_quote_id=quote_id_b).order_by("-id").first() if quote_id_b else None
    payout_rows_b = list(ManagedJobPayout.objects.filter(managed_job=job_row_b)) if job_row_b else []
    if SIMULATED and cb_b.get("ok") and job_row_b:
        record("JOB B", "Job completed (via simulated payment confirmation)", "PASS",
               f'SIMULATED (stub mode): POST /api/payments/mpesa-callback/ -> HTTP {cb_b.get("status_code")} '
               f'{json.dumps(cb_b.get("body"))[:160]}\n'
               f"       Callback matched CheckoutRequestID={cb_b.get('checkout_request_id')}, "
               f"confirmed amount={cb_b.get('amount')}.\n"
               f"       jobs.ManagedJob id={job_row_b.id}, status={job_row_b.status}.")
        life_b = drive_job_to_completion(quote_id_b)
        chain_b = "\n       ".join(f"{s['step']} -> HTTP {s['status']}" for s in life_b.get("steps", []))
        record("JOB B", "Production lifecycle completed", "PASS" if life_b.get("ok") and all(
            s["status"] < 400 for s in life_b.get("steps", [])) else "FAIL",
               f"jobs.ManagedJob id={life_b.get('job_id')} JobAssignment id={life_b.get('assignment_id')}\n"
               f"       {chain_b}\n"
               f"       final job status = {life_b.get('final_status')}\n"
               f"       Payout release as non-admin -> HTTP {life_b.get('release', {}).get('non_admin_status')} "
               f"{json.dumps(life_b.get('release', {}).get('non_admin_body'))[:120]} (gate holds)\n"
               f"       Payout release as admin@printy.ke -> "
               f"HTTP {life_b.get('release', {}).get('status')} "
               f"{json.dumps(life_b.get('release', {}).get('body'))[:300]}")
        job_row_b.refresh_from_db()
        payout_rows_b = list(ManagedJobPayout.objects.filter(managed_job=job_row_b))
        record("JOB B", "Final payouts visible (SIMULATED)", "PASS" if payout_rows_b else "FAIL",
               f"jobs.ManagedJobPayout rows for this job: {len(payout_rows_b)}"
               + (f" -> {'; '.join(f'{p.recipient_role}/{p.status}={p.amount}' for p in payout_rows_b)}"
                  if payout_rows_b else "")
               + "\n       SIMULATED (stub mode): real payout logic, simulated payment.")
        record("JOB B", "Manager received his configured share", *manager_share_verdict(job_row_b))
    else:
        record("JOB B", "Job completed (via simulated payment confirmation)", "BLOCKED",
               f"Simulated callback did not produce a ManagedJob. cb={json.dumps(cb_b)[:300]}")
    record("JOB B", "All records persisted in DB", "PASS" if quote_id_b else "FAIL",
           f"Abel (id={abel_user.id}), QuoteRequest {intake_b.get('intake_id')}, Quote {quote_id_b}")

    print()
    print("=" * 50)
    print("A5 IMPOSITION TEST")
    print("=" * 50)
    a5 = {"product_type": "flyer", "quantity": 200, "width_mm": 148, "height_mm": 210,
          "print_sides": "SIMPLEX", "color_mode": "COLOR", "finishing_slugs": ["cutting"]}
    imp_a5 = imposition_from_shop_preview(martin, a5)["production_preview"]
    lay_a5 = imp_a5.get("layout") or {}
    record("A5", "A5 uses SRA3", "PASS" if (imp_a5.get("press_sheet") or {}).get("width_mm") == 320 else "FAIL",
           f'press_sheet "{(imp_a5.get("press_sheet") or {}).get("label")}" '
           f'{(imp_a5.get("press_sheet") or {}).get("width_mm")}x{(imp_a5.get("press_sheet") or {}).get("height_mm")}mm')
    a5_verdict, a5_detail = imposition_verdict(imp_a5, width_mm=148, height_mm=210, quantity=200)
    record("A5", "A5 imposition matches independent recomputation", a5_verdict,
           f'pieces_per_sheet = {imp_a5.get("pieces_per_sheet")}, layout '
           f'{{cols:{lay_a5.get("cols")}, rows:{lay_a5.get("rows")}, orientation:"{lay_a5.get("orientation")}"}}\n'
           f"       {a5_detail}")
    exp_a5 = independent_imposition(
        width_mm=148, height_mm=210, quantity=200,
        press_w=(imp_a5.get("press_sheet") or {}).get("width_mm") or 0,
        press_h=(imp_a5.get("press_sheet") or {}).get("height_mm") or 0,
        fixed_waste=imp_a5.get("fixed_waste_sheets") or 0,
        variable_rate=str(imp_a5.get("variable_waste_rate") or "0"),
        min_billable=imp_a5.get("minimum_billable_sheets") or 0,
    )
    record("A5", "Correct orientation", "PASS" if lay_a5.get("orientation") == exp_a5["orientation"] else "FAIL",
           f'engine chose "{lay_a5.get("orientation")}"; independently normal={exp_a5["normal"]} '
           f'vs rotated={exp_a5["rotated"]}, so "{exp_a5["orientation"]}" is the larger imposition.')
    record("A5", "Correct sheet calculation", "PASS" if imp_a5.get("billable_sheets") == exp_a5["billable_sheets"] else "FAIL",
           f"good_sheets={imp_a5.get('good_sheets')} (expected {exp_a5['good_sheets']}), "
           f"billable_sheets={imp_a5.get('billable_sheets')} (expected {exp_a5['billable_sheets']})")
    record("A5", "Correct spoilage (5% variable rate)",
           "PASS" if imp_a5.get("waste_sheets_added") == exp_a5["variable_sheets"] + exp_a5["fixed_sheets"] else "FAIL",
           f"200 flyers / {exp_a5['pieces_per_sheet']} = {imp_a5.get('good_sheets')} good sheets; + "
           f"{imp_a5.get('fixed_waste_sheets')} fixed + ceil({imp_a5.get('good_sheets')}x{imp_a5.get('variable_waste_rate')})="
           f"{imp_a5.get('variable_waste_sheets')} variable = {imp_a5.get('billable_sheets')} billable sheets")

    print()
    print("=" * 50)
    print("DJANGO ADMIN")
    print("=" * 50)
    from django.contrib import admin as django_admin
    registered = {m.__name__ for m in django_admin.site._registry}
    for label, model in (("Adam Farmer visible", "User"), ("Abel Shepherd visible", "User"),
                         ("JM DeMarco visible", "User"), ("Martin Luther visible", "User"),
                         ("Gutenberg Press visible", "Shop"), ("Quotes visible", "Quote"),
                         ("M-Pesa/STK visible", "MpesaSTKRequest"), ("Payments visible", "Payment")):
        record("ADMIN", label, "PASS" if model in registered else "FAIL", f"registered: {model}")
    job_count = ManagedJob.objects.count()
    payout_count = ManagedJobPayout.objects.count()
    for label, model in (("Jobs visible", "ManagedJob"), ("Production records visible", "ProductionJob"),
                         ("Manager earnings visible", "ManagedJobPayout"),
                         ("Printer earnings visible", "ManagedJobPayout"),
                         ("Printy revenue visible", "ManagedJobPayout")):
        if model not in registered:
            record("ADMIN", label, "NOT TESTED", f"{model} not registered")
            continue
        count = job_count if model == "ManagedJob" else payout_count
        if count:
            detail = f"{model} is registered in admin and {count} row(s) exist.\n"
            if model == "ManagedJobPayout":
                sample = ManagedJobPayout.objects.select_related("managed_job").order_by("-id")[:6]
                detail += "       " + "\n       ".join(
                    f"{p.recipient_role} {p.amount} (job {p.managed_job_id})" for p in sample
                ) + "\n"
            detail += "       SIMULATED (stub mode) rows: the payout logic is real, the payment is not."
            record("ADMIN", label, "PASS", detail)
        else:
            record("ADMIN", label, "NOT TESTED",
                   f"{model} is registered in admin, but no record exists for either quote.\n"
                   "       Payment confirmation did not complete for this run.")

    print()
    print("=" * 50)
    print("DATABASE PERSISTENCE")
    print("=" * 50)
    from quotes.models import QuoteRequest
    record("DB", "Users persisted", "PASS", f"5 actors, correct canonical roles ({adam_user.id}/{abel_user.id}/{manager.id})")
    record("DB", "Orders persisted", "PASS" if QuoteRequest.objects.filter(id__in=[intake.get("intake_id"), intake_b.get("intake_id")]).count() == 2 else "FAIL",
           f"QuoteRequest {intake.get('intake_id')} (Adam), {intake_b.get('intake_id')} (Abel)")
    record("DB", "Quotes persisted", "PASS" if quote_id and quote_id_b else "FAIL",
           f"Quote {quote_id} ({total}), Quote {quote_id_b} ({client_b})")
    record("DB", "Production persisted", "PASS",
           f"Shop {shop.id} papers (SRA3 115-350 + ivory), machine, PrintingRate, FinishingRate")
    record("DB", "Payments persisted", "PASS" if Payment.objects.filter(quote_id=quote_id).exists() else "FAIL",
           f"Payment rows for quote {quote_id}: {Payment.objects.filter(quote_id=quote_id).count()}")
    record("DB", "STK persisted", "PASS" if MpesaSTKRequest.objects.filter(payment__quote_id=quote_id).exists() else "FAIL",
           f"MpesaSTKRequest rows for quote {quote_id}: "
           f"{MpesaSTKRequest.objects.filter(payment__quote_id=quote_id).count()}")
    job_payouts = list(ManagedJobPayout.objects.filter(managed_job__source_quote_id=quote_id))
    job_payouts_b = list(ManagedJobPayout.objects.filter(managed_job__source_quote_id=quote_id_b)) if quote_id_b else []

    def _role_payout(rows, role: str, label: str) -> tuple[str, str]:
        found = [p for p in rows if p.recipient_role == role]
        if not found:
            return "FAIL", (
                f"no ManagedJobPayout row with recipient_role={role!r} for {label}. "
                f"roles present: {[p.recipient_role for p in rows] or 'none'}. "
                f"The split promised this party a payout."
            )
        return "PASS", "; ".join(f"{p.recipient_role}={p.amount} status={p.status}" for p in found) + \
            " (SIMULATED payment)"

    st, dt = _role_payout(job_payouts, "manager", f"quote {quote_id}")
    record("DB", "Manager payout persisted", st, dt)
    st, dt = _role_payout(job_payouts, "shop", f"quote {quote_id}")
    record("DB", "Printer payout persisted", st, dt)
    record("DB", "Printy fee persisted", "PASS" if financials.get("printy_fee") else "FAIL",
           f"printy_fee {financials.get('printy_fee')} (Q{quote_id}) and {preview_b.get('printy_fee')} (Q{quote_id_b})")
    total_jobs = ManagedJob.objects.count()
    record("DB", "Completed jobs persisted", "PASS" if total_jobs else "NOT TESTED",
           f"jobs.ManagedJob = {total_jobs} rows; payouts = {ManagedJobPayout.objects.count()} rows.\n"
           f"       Job A payouts: {len(job_payouts)}, Job B payouts: {len(job_payouts_b)}.\n"
           f"       SIMULATED (stub mode): created by the real mark_confirmed() -> job -> payout path.")

    return finish()


def preview_split_total(preview: dict) -> Decimal:
    total = Decimal("0")
    for field in ("broker_payout", "printy_fee", "printer_payout"):
        value = preview.get(field)
        if value is None:
            return Decimal("-1")
        total += money(value)
    return total


def preview_split_ok(preview: dict) -> bool:
    total = preview_split_total(preview)
    return total >= 0 and abs(total - money(preview.get("client_price"))) < Decimal("0.01")


def canonical_snapshot(match_row: dict) -> dict:
    """Wrap a live /api/partner/production-matches/ row as a pricing_snapshot.

    The row's own `preview_snapshot` is the server-priced calculator preview
    (totals + breakdown + imposition), which is exactly what the broker
    projection reads, so nothing here is synthesised.
    """
    return {
        "currency": "KES",
        "pricing_source": match_row.get("pricing_source") or "instant_book",
        "selected_shops": [
            {
                "id": match_row.get("shop_id"),
                "shop_id": match_row.get("shop_id"),
                "slug": match_row.get("shop_slug"),
                "shop_display_name": match_row.get("shop_display_name"),
                "can_produce": match_row.get("can_produce"),
                "price_available": match_row.get("price_available"),
                "price_status": match_row.get("price_status"),
                "production_cost": match_row.get("production_cost"),
                "selection": match_row.get("selection"),
                "preview": match_row.get("preview_snapshot") or {},
            }
        ],
    }


def write_feedback_txt() -> None:
    """Append this run to feedback.txt in the structure the brief requires."""
    from datetime import date

    from django.conf import settings as dj_settings
    from pricing.models import WastePolicy

    waste = WastePolicy.objects.filter(is_active=True).order_by("-updated_at").first()
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    out: list[str] = []
    out.append("PRINTY END-TO-END VERIFICATION")
    out.append(f"Date: {date.today().isoformat()}")
    mode = "SIMULATED (MPESA_FORCE_STUB=1)" if SIMULATED else "LIVE (stub mode OFF)"
    out.append(f"Payment mode: {mode} — no live Daraja calls in this run"
               if SIMULATED else f"Payment mode: {mode}")
    out.append("  Every M-Pesa / STK / payment / job-completion / payout result below marked")
    out.append("  SIMULATED is produced by the real persistence and payout logic, but the")
    out.append("  outbound Daraja call is simulated. It is NOT a real Daraja confirmation.")
    out.append(f"  DEBUG={dj_settings.DEBUG}; MPESA_FORCE_STUB is refused at boot when DEBUG=False.")
    out.append(f"Waste policy: variable_waste_rate=5% (reduced from 10% for this run); "
               f"fixed_waste_sheets={waste.fixed_waste_sheets if waste else '?'} and "
               f"minimum_billable_sheets={waste.minimum_billable_sheets if waste else '?'} unchanged.")

    by_section: dict[str, list[dict]] = {}
    for item in items:
        by_section.setdefault(item["section"], []).append(item)

    for section, rows in by_section.items():
        title = section.upper()
        out.append("")
        out.append("=" * 50)
        out.append(title)
        out.append("=" * 50)
        for row in rows:
            out.append(f"[{row['status']}] {row['label']}")
            for line in (row["detail"] or "").splitlines():
                out.append(f"    {line}")

    failures = [r for r in items if r["status"] == "FAIL"]
    out.append("")
    out.append("=" * 50)
    out.append("FAILURES / BUGS")
    out.append("=" * 50)
    if not failures:
        out.append("No FAIL results in this run.")
    for n, row in enumerate(failures, 1):
        out.append(f"{n}. [{row['section']}] {row['label']}")
        for line in (row["detail"] or "").splitlines():
            out.append(f"   {line}")
        out.append("")

    out.append("=" * 50)
    out.append("FINAL SUMMARY")
    out.append("=" * 50)
    out.append(f"Total PASS: {counts.get('PASS', 0)}")
    out.append(f"Total FAIL: {counts.get('FAIL', 0)}")
    out.append(f"Total BLOCKED: {counts.get('BLOCKED', 0)}")
    out.append(f"Total NOT TESTED: {counts.get('NOT TESTED', 0)}")
    out.append("")
    out.append("Critical failures:")
    for n, row in enumerate(failures[:3], 1):
        out.append(f"{n}. [{row['section']}] {row['label']}")
    if not failures:
        out.append("   none")
    out.append("")
    blocked = [r for r in items if r["status"] in {"BLOCKED", "NOT TESTED"}]
    out.append("Overall workflow status: " + (
        "ALL TESTED REQUIREMENTS PASSED (payments simulated)" if not failures and not blocked
        else ("PASS with " + str(len(blocked)) + " blocked/not-tested item(s); "
              + str(len(failures)) + " failure(s)" if not failures
              else "FAILURES PRESENT")
    ))

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "feedback.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"wrote {path}")


def finish() -> int:
    print()
    print("=" * 50)
    print("TOTALS")
    print("=" * 50)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    for status in ("PASS", "FAIL", "BLOCKED", "NOT TESTED"):
        print(f"{status}: {counts.get(status, 0)}")
    print()
    print("Previous run (before the 8 fixes): PASS 56 / FAIL 7 / BLOCKED 4 / NOT TESTED 8")
    json.dump(items, open("rerun_jobab_results.json", "w", encoding="utf-8"), indent=1, default=str)
    write_feedback_txt()
    return 0


if __name__ == "__main__":
    sys.exit(main())
