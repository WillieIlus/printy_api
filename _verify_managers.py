"""Throwaway verification for the derived "my managers" list and the featured managers.

Runs on a throwaway sqlite file (config.test_settings + SQLITE_DATABASE_NAME).
"""
from __future__ import annotations

import json
import os
import tempfile

DB_PATH = os.path.join(tempfile.gettempdir(), "printy_managers_verify.sqlite3")
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
from accounts.services.manager_relationships import managers_for_client  # noqa: E402
from jobs.models import ManagedJob  # noqa: E402
from quotes.models import QuoteRequest  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (" :: " + detail if detail else ""))


def profile_of(user) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def manager(email: str, *, name: str, first_name: str = "", verified=False, opt_in=False, photo="", speciality=""):
    user = User.objects.create(email=email, role=User.Role.BROKER, name=name, first_name=first_name)
    profile = profile_of(user)
    profile.manager_verified = verified
    profile.manager_public_opt_in = opt_in
    profile.avatar = photo
    profile.manager_speciality = speciality
    profile.save()
    return user


def paid_job(client, broker, payment_status="confirmed", status="completed"):
    request = QuoteRequest.objects.create(
        created_by=broker,
        assigned_manager=broker,
        created_by_manager=broker,
        customer_name="Someone",
    )
    return ManagedJob.objects.create(
        source_quote_request=request,
        client=client,
        broker=broker,
        status=status,
        payment_status=payment_status,
    )


# ------------------------------------------------------- A. the derived manager list
client = User.objects.create(email="client@example.test", role=User.Role.CLIENT, name="Cathy Client")
origin = manager("origin@example.test", name="Olive Origin", first_name="Olive")
worker = manager("worker@example.test", name="Wes Worker", first_name="Wes")
unpaid = manager("unpaid@example.test", name="Uma Unpaid", first_name="Uma")
refunded = manager("refunded@example.test", name="Rita Refunded", first_name="Rita")

profile = profile_of(client)
profile.origin_manager = origin
profile.save(update_fields=["origin_manager"])

paid_job(client, worker, "confirmed")
paid_job(client, worker, "released")
paid_job(client, origin, "release_ready")
paid_job(client, unpaid, "pending")
paid_job(client, refunded, "refunded", status="cancelled")

rows = managers_for_client(client)
ids = [row["id"] for row in rows]
check("origin manager is listed", origin.id in ids)
check("manager with a paid job is listed", worker.id in ids)
check("manager with only an unpaid job is not listed", unpaid.id not in ids)
check("refunded job does not count as paid", refunded.id not in ids)
check("origin sorts first", ids and ids[0] == origin.id, str(ids))
check("a manager appears once", len(ids) == len(set(ids)), str(ids))
origin_row = next(row for row in rows if row["id"] == origin.id)
check("origin row records both sources", origin_row["sources"] == ["origin", "paid_job"], str(origin_row["sources"]))
check("origin row counts paid jobs", origin_row["paid_jobs"] == 1, str(origin_row["paid_jobs"]))
worker_row = next(row for row in rows if row["id"] == worker.id)
check("worked-with row counts paid jobs", worker_row["paid_jobs"] == 2, str(worker_row["paid_jobs"]))
check("rows carry a first name", worker_row["first_name"] == "Wes", str(worker_row["first_name"]))

blob = json.dumps(rows, default=str).lower()
for forbidden in ("amount", "fee", "margin", "cost", "price", "shop", "payout", "revenue"):
    check(f"my-managers hides {forbidden}", forbidden not in blob)

# the derived list must not write anything that a fee decision reads
profile.refresh_from_db()
check("origin manager is unchanged by the read", profile.origin_manager_id == origin.id)
before = list(UserProfile.objects.values_list("pk", "origin_manager_id", "manager_verified"))
managers_for_client(client)
after = list(UserProfile.objects.values_list("pk", "origin_manager_id", "manager_verified"))
check("deriving the list writes nothing", before == after)

# ------------------------------------------------------------------- B. the endpoint
api = APIClient()
api.force_authenticate(user=client)
response = api.get("/api/client-portal/my-managers/")
body = response.json()
check("my-managers returns 200", response.status_code == 200, str(response.status_code))
check("my-managers returns the derived rows", body.get("count") == len(rows), str(body.get("count")))
check("my-managers is not shareable cache", "no-store" in response.get("Cache-Control", ""), response.get("Cache-Control", ""))

api.force_authenticate(user=worker)
forbidden_response = api.get("/api/client-portal/my-managers/")
check("a manager account gets 403", forbidden_response.status_code == 403, str(forbidden_response.status_code))

api.force_authenticate(user=None)
check("signed out gets 401/403", api.get("/api/client-portal/my-managers/").status_code in (401, 403))

# ------------------------------------------------------------ C. featured managers
def featured():
    anon = APIClient()
    return anon.get("/api/public/print-managers/featured/")


public_ok = [
    manager("f1@example.test", name="Fay One", first_name="Fay", verified=True, opt_in=True, photo="https://cdn.example/f1.jpg", speciality="Booklets"),
    manager("f2@example.test", name="Femi Two", first_name="Femi", verified=True, opt_in=True, photo="https://cdn.example/f2.jpg", speciality="Flyers"),
]
response = featured()
check("two qualifying managers return nothing", response.status_code == 200 and response.json()["results"] == [], str(response.json()))

public_ok.append(
    manager("f3@example.test", name="Flo Three", first_name="Flo", verified=True, opt_in=True, photo="https://cdn.example/f3.jpg", speciality="Same-day posters")
)
# each of these is missing exactly one condition and must be excluded
manager("nv@example.test", name="Nia NoVerify", verified=False, opt_in=True, photo="https://cdn.example/nv.jpg", speciality="Cards")
manager("no@example.test", name="Ned NoOptIn", verified=True, opt_in=False, photo="https://cdn.example/no.jpg", speciality="Cards")
manager("np@example.test", name="Nora NoPhoto", verified=True, opt_in=True, photo="", speciality="Cards")
manager("ns@example.test", name="Nate NoSpeciality", verified=True, opt_in=True, photo="https://cdn.example/ns.jpg", speciality="")
inactive = manager("ia@example.test", name="Ivy Inactive", verified=True, opt_in=True, photo="https://cdn.example/ia.jpg", speciality="Cards")
inactive.is_active = False
inactive.save(update_fields=["is_active"])
shop_owner = User.objects.create(email="owner@example.test", role=User.Role.SHOP_OWNER, name="Sam Shop", first_name="Sam")
shop_profile = profile_of(shop_owner)
shop_profile.manager_verified = True
shop_profile.manager_public_opt_in = True
shop_profile.avatar = "https://cdn.example/sam.jpg"
shop_profile.manager_speciality = "Offset"
shop_profile.save()

response = featured()
body = response.json()
results = body.get("results") or []
names = {row["first_name"] for row in results}
check("three qualifying managers are returned", len(results) == 3, str(len(results)))
check("only the qualifying managers appear", names == {"Fay", "Femi", "Flo"}, str(sorted(names)))
check("unverified is excluded", "Nia" not in names)
check("not opted in is excluded", "Ned" not in names)
check("no photo is excluded", "Nora" not in names)
check("no speciality is excluded", "Nate" not in names)
check("inactive manager is excluded", "Ivy" not in names)
check("non-manager role is excluded", "Sam" not in names)

row_keys = {tuple(sorted(row.keys())) for row in results}
check("payload has exactly the four allowed fields", row_keys == {("first_name", "photo", "speciality", "verified")}, str(row_keys))
check("verified flag is true", all(row["verified"] is True for row in results))
check("speciality is a single line", all("\n" not in row["speciality"] for row in results))
blob = json.dumps(results).lower()
for forbidden in ("price", "rating", "ranking", "shop", "printer", "surname", "score"):
    check(f"featured hides {forbidden}", forbidden not in blob)
check("featured is not cached", "no-store" in response.get("Cache-Control", ""), response.get("Cache-Control", ""))

orders = {tuple(featured().json()["results"][i]["first_name"] for i in range(3)) for _ in range(20)}
check("order is randomized per request", len(orders) > 1, f"{len(orders)} distinct orders in 20 calls")

failed = [name for name, ok, _ in checks if not ok]
print("\n%d checks, %d failed" % (len(checks), len(failed)))
if failed:
    for name in failed:
        print("  FAILED:", name)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
