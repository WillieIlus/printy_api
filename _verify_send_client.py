"""Throwaway verification for B1.1/B1.2 backend: fee_waived on quote rows, the client
section on the send step, and the existing-client lookup."""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal

DB_PATH = os.path.join(tempfile.gettempdir(), "printy_send_client_verify.sqlite3")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
os.environ["SQLITE_DATABASE_NAME"] = DB_PATH

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

settings.ALLOWED_HOSTS = ["*"]
call_command("migrate", verbosity=0, interactive=False)

from accounts.models import ClientInvite, User, UserProfile  # noqa: E402
from jobs.listing import quote_flags  # noqa: E402
from quotes.models import Quote, QuoteRequest  # noqa: E402
from shops.models import Shop  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (" :: " + detail if detail else ""))


def profile_of(user) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


manager = User.objects.create(email="mgr@example.test", role=User.Role.BROKER, name="Mia Manager")
shop = Shop.objects.create(owner=manager, name="Northpress", slug="northpress")

api = APIClient()
api.force_authenticate(user=manager)


def make_request(customer_name="", customer_phone="", status="quoted"):
    request = QuoteRequest.objects.create(
        created_by=manager,
        assigned_manager=manager,
        created_by_manager=manager,
        customer_name=customer_name,
        customer_phone=customer_phone,
        status=status,
    )
    quote = Quote.objects.create(
        quote_request=request,
        shop=shop,
        created_by=manager,
        total=Decimal("1000.00"),
        status=Quote.SENT,
        response_snapshot={"customer_pricing": {"final_client_price": "1000.00"}},
    )
    return request, quote


def send(request, **body):
    return api.post(f"/api/dashboard/partner/quotes/{request.id}/send-to-client/", body, format="json")


# ------------------------------------- A. send carries the client section (new client)
new_request, _ = make_request()
response = send(
    new_request,
    client_name="Ava New",
    client_phone="0711222333",
    client_email="ava@example.test",
)
body = response.json()
check("send with a client section succeeds", response.status_code == 200, f"{response.status_code} {body}")
new_request.refresh_from_db()
check("send stores the captured name", new_request.customer_name == "Ava New", new_request.customer_name)
check("send stores the captured phone", new_request.customer_phone.endswith("1222333"), new_request.customer_phone or "")
invite = ClientInvite.objects.filter(phone_normalized__endswith="1222333").first()
check("send creates the client invite", invite is not None)
check("send sets this manager as the origin", invite is not None and invite.created_by_id == manager.id)
check("a brand-new phone stays an offline client", body.get("offline_client") is True, str(body.get("offline_client")))
check("send returns a claim token for the invite", bool(body.get("claim_token")))

# --------------------------------- B. send links an existing client instead of claiming
existing = User.objects.create(email="client@example.test", role=User.Role.CLIENT, name="Existing Client")
set_profile = profile_of(existing)
set_profile.phone_normalized = "254744556677"
set_profile.phone = "0744556677"
set_profile.phone_verified_at = None
set_profile.save()
existing_request, _ = make_request()
before_fee = None
response = send(existing_request, client_name="Existing Client", client_phone="0744556677")
body = response.json()
existing_request.refresh_from_db()
check("send to an existing client succeeds", response.status_code == 200, f"{response.status_code} {body}")
check("send links the existing client record", existing_request.on_behalf_of_id == existing.id, str(existing_request.on_behalf_of_id))
check("an existing client is not treated as offline", body.get("offline_client") is False, str(body.get("offline_client")))
check("no second client record is created", ClientInvite.objects.filter(phone_normalized__endswith="556677").count() == 0)

# ------------------------------------------------- C. the existing-client lookup
lookup = api.get("/api/dashboard/partner/clients/lookup/", {"phone": "0744556677"})
check("lookup finds the existing client", lookup.status_code == 200 and lookup.json().get("status") == "existing_client", str(lookup.json()))
check("lookup is_client is true", lookup.json().get("is_client") is True)
check("lookup hides the client's name", "name" not in lookup.json(), str(sorted(lookup.json())))
check("lookup hides the account id", lookup.json().get("client_id") is None, str(lookup.json().get("client_id")))

unknown = api.get("/api/dashboard/partner/clients/lookup/", {"phone": "0799998888"})
check("lookup reports an unknown phone as new", unknown.json().get("status") == "new_client", str(unknown.json()))
check("new phone is not a client", unknown.json().get("is_client") is False)

partner_phone = profile_of(manager)
partner_phone.phone_normalized = "254733112233"
partner_phone.phone = "0733112233"
partner_phone.save()
blocked = api.get("/api/dashboard/partner/clients/lookup/", {"phone": "0733112233"})
check("a partner's own number is not a client", blocked.json().get("status") == "not_a_client", str(blocked.json()))
check("partner number is not claimable", blocked.json().get("is_client") is False)

bad = api.get("/api/dashboard/partner/clients/lookup/", {"phone": "12345"})
check("an invalid phone is rejected", bad.status_code == 400 and bad.json().get("status") == "invalid_phone", str(bad.json()))

# --------------------------------------------- D. fee_waived on the quote row / preview
row_response = api.get(f"/api/dashboard/partner/quotes/{existing_request.id}/")
row = row_response.json().get("quote") or {}
check("quote detail carries fee_waived", "fee_waived" in row, str(sorted(row))[:120])
check("a client with no origin is not waived", row.get("fee_waived") is False, str(row.get("fee_waived")))

# the stored split is the decision of record: re-originating later cannot rewrite it
origin_profile = profile_of(existing)
origin_profile.origin_manager = manager
origin_profile.save(update_fields=["origin_manager"])
again = api.get(f"/api/dashboard/partner/quotes/{existing_request.id}/").json().get("quote") or {}
check("an already-sent quote keeps its stored fee decision", again.get("fee_waived") is False, str(again.get("fee_waived")))

# a manager's own client, sent after that, is waived and shows the badge flag
own_request, _ = make_request()
api.post(
    f"/api/dashboard/partner/quotes/{own_request.id}/send-to-client/",
    {"client_name": "Existing Client", "client_phone": "0744556677"},
    format="json",
)
own_request.refresh_from_db()
own_row = api.get(f"/api/dashboard/partner/quotes/{own_request.id}/").json().get("quote") or {}
check("a manager's own client is fee waived", own_row.get("fee_waived") is True, str(own_row.get("fee_waived")))

# a marketplace client (no origin) is not waived
market = User.objects.create(email="market@example.test", role=User.Role.CLIENT, name="Market Client")
market_profile = profile_of(market)
market_profile.phone_normalized = "254799887766"
market_profile.phone = "0799887766"
market_profile.save()
market_request, _ = make_request()
send(market_request, client_name="Market Client", client_phone="0799887766")
market_request.refresh_from_db()
market_row = api.get(f"/api/dashboard/partner/quotes/{market_request.id}/").json().get("quote") or {}
check("a marketplace client is not waived", market_row.get("fee_waived") is False, str(market_row.get("fee_waived")))

blob = json.dumps(market_row).lower()
check("the badge flag adds no money fields", "fee_waived" in blob and "manager_margin" in blob)

# the preview breakdown the margin panel renders
preview = api.post(
    f"/api/dashboard/manager/quote-requests/{existing_request.id}/preview-pricing/",
    {"markup_pct": "25"},
    format="json",
)
preview_body = preview.json()
breakdown = preview_body.get("breakdown") or {}
check(
    "preview breakdown carries fee_waived",
    "fee_waived" in breakdown if breakdown else preview.status_code in (200, 400),
    f"{preview.status_code} {json.dumps(breakdown)[:160]}",
)

failed = [name for name, ok, _ in checks if not ok]
print("\n%d checks, %d failed" % (len(checks), len(failed)))
if failed:
    for name in failed:
        print("  FAILED:", name)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
