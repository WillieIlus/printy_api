"""Throwaway verification for the origin_manager / fee_waived work.

Runs on a throwaway sqlite file (config.test_settings + SQLITE_DATABASE_NAME).
Checks: the fee maths, the origin decision, the immutability guard, the split
snapshot, and the job copy.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal

DB_PATH = os.path.join(tempfile.gettempdir(), "printy_origin_fee_verify.sqlite3")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
os.environ["SQLITE_DATABASE_NAME"] = DB_PATH

import django  # noqa: E402

django.setup()

from django.core.exceptions import ValidationError  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.utils import timezone  # noqa: E402

call_command("migrate", verbosity=0, interactive=False)

from accounts.models import User, UserProfile  # noqa: E402
from accounts.services.phone import set_origin_manager  # noqa: E402
from jobs.managed_services import create_managed_job_from_accepted_quote  # noqa: E402
from pricing.services.fee_waiver import (  # noqa: E402
    fee_waived_for_job,
    fee_waived_for_pair,
    fee_waived_for_quote,
    fee_waived_for_quote_request,
    is_client_origin_pair,
)
from pricing.services.platform_fee_policy import (  # noqa: E402
    calculate_financial_split,
    create_quote_financial_split,
    ensure_quote_financial_split,
    get_active_platform_fee_policy,
)
from quotes.models import Quote, QuoteRequest  # noqa: E402
from shops.models import Shop  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (" :: " + detail if detail else ""))


policy = get_active_platform_fee_policy()

# ---------------------------------------------------------------- 1. the maths
for base, markup in ((1000, 600), (5000, 1000), (25000, 4000)):
    normal = calculate_financial_split(production_cost=base, manager_markup=markup, policy=policy)
    waived = calculate_financial_split(production_cost=base, manager_markup=markup, policy=policy, fee_waived=True)
    tag = f"base={base} markup={markup}"
    check(f"client_total unchanged [{tag}]", normal.client_total == waived.client_total, str(waived.client_total))
    check(f"shop_payout unchanged [{tag}]", normal.shop_payout == waived.shop_payout, str(waived.shop_payout))
    check(
        f"printer-side fee unchanged [{tag}]",
        normal.printer_side_fee == waived.printer_side_fee,
        str(waived.printer_side_fee),
    )
    check(f"printy_fee zero when waived [{tag}]", waived.printy_fee == Decimal("0.00"), str(waived.printy_fee))
    check(
        f"manager keeps the fee [{tag}]",
        waived.manager_payout == waived.client_total - waived.shop_payout,
        f"{waived.manager_payout} vs {waived.client_total - waived.shop_payout}",
    )
    check(
        f"split still reconciles [{tag}]",
        waived.shop_payout + waived.manager_payout + waived.printy_fee == waived.client_total,
        str(waived.client_total),
    )
    check(f"normal fee still charged without waiver [{tag}]", normal.printy_fee > 0, str(normal.printy_fee))
    check(f"waiver flag carried on the result [{tag}]", waived.fee_waived is True and normal.fee_waived is False)

# ------------------------------------------------------- 2. accounts and origin
manager = User.objects.create(email="manager-owner@example.test", role=User.Role.BROKER, name="Owner Manager")
other_manager = User.objects.create(email="manager-other@example.test", role=User.Role.BROKER, name="Other Manager")
client = User.objects.create(email="client-owned@example.test", role=User.Role.CLIENT, name="Owned Client")
market_client = User.objects.create(email="client-market@example.test", role=User.Role.CLIENT, name="Market Client")

owned_profile = UserProfile.objects.create(
    user=client,
    phone="+254700000001",
    phone_normalized="254700000001",
    phone_verified_at=timezone.now(),
    origin_manager=manager,
)
UserProfile.objects.create(
    user=market_client,
    phone="+254700000002",
    phone_normalized="254700000002",
    phone_verified_at=timezone.now(),
)

check("origin pair recognised", is_client_origin_pair(client=client, manager=manager))
check("different manager is not the origin", not is_client_origin_pair(client=client, manager=other_manager))
check("marketplace client has no origin", not is_client_origin_pair(client=market_client, manager=manager))
check("marketplace client waiver decision False", fee_waived_for_pair(client=market_client, manager=manager) is False)
check("no manager means no waiver", fee_waived_for_pair(client=client, manager=None) is False)

owned_profile.origin_manager = other_manager
try:
    owned_profile.save()
    check("re-originating is rejected", False, "save() allowed it")
except ValidationError as exc:
    check("re-originating is rejected", True, str(exc.message_dict.get("origin_manager")))

owned_profile.refresh_from_db()
check("origin unchanged after refused save", owned_profile.origin_manager_id == manager.id)

owned_profile.origin_manager = None
try:
    owned_profile.save()
    check("clearing the origin is rejected", False, "save() allowed it")
except ValidationError:
    check("clearing the origin is rejected", True)

owned_profile.refresh_from_db()
check("origin still set after refused clear", owned_profile.origin_manager_id == manager.id)

set_origin_manager(client, other_manager)
owned_profile.refresh_from_db()
check("set_origin_manager never re-originates", owned_profile.origin_manager_id == manager.id)

fresh = User.objects.create(email="client-fresh@example.test", role=User.Role.CLIENT, name="Fresh Client")
set_origin_manager(fresh, manager)
check("first stamp is allowed", UserProfile.objects.get(user=fresh).origin_manager_id == manager.id)

# --------------------------------------------------- 3. quote split + job copy
shop = Shop.objects.create(owner=manager, name="Verify Shop", slug="verify-shop")
quote_request = QuoteRequest.objects.create(
    created_by=manager,
    assigned_manager=manager,
    created_by_manager=manager,
    on_behalf_of=client,
    customer_name="Owned Client",
    customer_phone="254700000001",
    status=QuoteRequest.QUOTED,
)
market_request = QuoteRequest.objects.create(
    created_by=manager,
    assigned_manager=manager,
    created_by_manager=manager,
    on_behalf_of=market_client,
    customer_name="Market Client",
    customer_phone="254700000002",
    status=QuoteRequest.QUOTED,
)
check("quote request decision: own client", fee_waived_for_quote_request(quote_request) is True)
check("quote request decision: market client", fee_waived_for_quote_request(market_request) is False)

quote = Quote.objects.create(quote_request=quote_request, shop=shop, created_by=manager, total=Decimal("1600.00"), status=Quote.SENT)
split = create_quote_financial_split(quote=quote, production_cost=Decimal("1000.00"), broker_client_price=Decimal("1600.00"))
check("split freezes the decision", split.fee_waived is True)
check("split charges no printy fee", split.printy_fee == Decimal("0.00"), str(split.printy_fee))
check(
    "split pays the manager the fee",
    split.manager_payout == split.client_total - split.shop_payout,
    str(split.manager_payout),
)
_unwaived = calculate_financial_split(
    production_cost=Decimal("1000.00"), manager_markup=Decimal("600.00"), policy=policy
)
check(
    "split printer side untouched",
    split.printer_side_fee == _unwaived.printer_side_fee and split.shop_payout == _unwaived.shop_payout,
    str(split.printer_side_fee),
)
check("quote reads the stored decision", fee_waived_for_quote(quote) is True)

requote = create_quote_financial_split(
    quote=quote,
    production_cost=Decimal("1000.00"),
    broker_client_price=Decimal("1700.00"),
)
check("recalculated split keeps the frozen decision", requote.fee_waived is True)
check("recalculated split still waives", requote.printy_fee == Decimal("0.00"), str(requote.printy_fee))

quote.total = Decimal("1700.00")
quote.status = Quote.ACCEPTED
quote.accepted_at = timezone.now()
quote.save(update_fields=["total", "status", "accepted_at"])
locked = ensure_quote_financial_split(quote=quote)
check("acceptance keeps the decision", locked.fee_waived is True)
check("acceptance locks the split", locked.locked is True)
check("accepted amount is the client price", locked.client_total == Decimal("1700.00"), str(locked.client_total))

market_quote = Quote.objects.create(
    quote_request=market_request, shop=shop, created_by=manager, total=Decimal("1600.00"), status=Quote.SENT
)
market_split = create_quote_financial_split(
    quote=market_quote, production_cost=Decimal("1000.00"), broker_client_price=Decimal("1600.00")
)
check("marketplace quote keeps the normal fee", market_split.fee_waived is False and market_split.printy_fee > 0, str(market_split.printy_fee))

try:
    job = create_managed_job_from_accepted_quote(quote_request=quote_request, quote=quote, accepted_by=manager)
    check("job inherits the decision", job.fee_waived is True)
    check("job keeps the waived fee", job.printy_fee == Decimal("0.00"), str(job.printy_fee))
    check("job totals match the split", job.client_total == locked.client_total and job.broker_payout == locked.manager_payout)
    check("job reads the frozen flag", fee_waived_for_job(job) is True)
except Exception as exc:  # noqa: BLE001 - reported, not hidden
    check("job creation from accepted quote", False, f"{type(exc).__name__}: {exc}")

failed = [name for name, ok, _ in checks if not ok]
print("\n%d checks, %d failed" % (len(checks), len(failed)))
if failed:
    for name in failed:
        print("  FAILED:", name)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
