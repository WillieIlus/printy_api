"""Throwaway verification for B1.3 backend.

Checks that a signed-in client's own managers are pinned first in the
recommended-managers list with is_client_manager set, that Verified is a separate
flag, and that no fee/margin field reaches the client payload.
"""
from __future__ import annotations

import json
import os
import tempfile

DB_PATH = os.path.join(tempfile.gettempdir(), "printy_client_pin_verify.sqlite3")
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

from accounts.models import User, UserProfile  # noqa: E402
from jobs.models import ManagedJob  # noqa: E402
from quotes.models import QuoteRequest  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (" :: " + detail if detail else ""))


def profile_of(user) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def make_manager(email: str, name: str, *, verified: bool = False) -> User:
    user = User.objects.create(
        email=email,
        role=User.Role.BROKER,
        name=name,
        partner_profile_enabled=True,
    )
    profile = profile_of(user)
    profile.manager_verified = verified
    profile.save()
    return user


origin = make_manager("origin@example.test", "Olive Origin", verified=True)
worker = make_manager("worker@example.test", "Wes Worker")
stranger = make_manager("stranger@example.test", "Sam Stranger")
client = User.objects.create(email="client@example.test", role=User.Role.CLIENT, name="Cathy Client")

client_profile = profile_of(client)
client_profile.origin_manager = origin
client_profile.save(update_fields=["origin_manager"])

request = QuoteRequest.objects.create(
    created_by=worker, assigned_manager=worker, created_by_manager=worker, customer_name="Cathy",
)
ManagedJob.objects.create(
    source_quote_request=request,
    client=client,
    broker=worker,
    status="completed",
    payment_status="released",
)

api = APIClient()
api.force_authenticate(user=client)
query = {"product_type": "business_card", "quantity": 500}
response = api.get("/api/intake/recommended-managers/", query)
body = response.json()
check("recommended managers returns 200", response.status_code == 200, str(response.status_code))
rows = body.get("results") or []
ids = [row["id"] for row in rows]

check("the list is not empty", bool(rows), str(ids))
check("origin manager is pinned first", ids[:1] == [origin.id], str(ids))
check("the manager with paid work is pinned next", ids[1:2] == [worker.id], str(ids))
check("a stranger manager is not pinned", stranger.id in ids and rows[ids.index(stranger.id)].get("is_client_manager") is False, str(ids))
check("origin row is flagged as a client manager", rows[0].get("is_client_manager") is True)
check("paid-work row is flagged as a client manager", rows[1].get("is_client_manager") is True)
check("verified is a separate flag (origin)", rows[0].get("is_verified") is True)
check("verified is a separate flag (worker)", rows[1].get("is_verified") is False)
check("stranger is unverified", rows[ids.index(stranger.id)].get("is_verified") is False)
check("pinning does not duplicate a manager", len(ids) == len(set(ids)), str(ids))

blob = json.dumps(rows).lower()
for forbidden in ("printy_fee", "platform_fee", "margin", "markup", "payout", "fee_waived", "client_total"):
    check(f"client manager list carries no {forbidden}", forbidden not in blob)

# A signed-out visitor's public list keeps its normal order and pins nobody.
anon = APIClient()
public_response = anon.get("/api/public/print-managers/recommended/", query)
public_body = public_response.json()
public_rows = public_body.get("results") or []
check("public recommended list still returns 200", public_response.status_code == 200, str(public_response.status_code))
check(
    "public list pins nobody",
    all(not row.get("is_client_manager") for row in public_rows),
    str([(row.get("id"), row.get("is_client_manager")) for row in public_rows]),
)

# The client's derived manager list (B1.3 dashboard panel) carries no money either.
mine = api.get("/api/client-portal/my-managers/")
mine_body = mine.json()
check("my-managers returns 200", mine.status_code == 200, str(mine.status_code))
check("my-managers pins origin first", [row["id"] for row in mine_body["results"]][:1] == [origin.id])
mine_blob = json.dumps(mine_body).lower()
for forbidden in ("fee", "margin", "payout", "kes", "total"):
    check(f"my-managers carries no {forbidden}", forbidden not in mine_blob)

# A manager calling the client list gets nothing and sees no client data.
api.force_authenticate(user=worker)
forbidden = api.get("/api/client-portal/my-managers/")
check("a manager cannot read a client's manager list", forbidden.status_code == 403, str(forbidden.status_code))

failed = [name for name, ok, _ in checks if not ok]
print("\n%d checks, %d failed" % (len(checks), len(failed)))
if failed:
    for name in failed:
        print("  FAILED:", name)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
