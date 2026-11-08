"""Throwaway verification for the manager-adds-client / anonymous-pay law.

Runs on a throwaway sqlite file (config.test_settings + SQLITE_DATABASE_NAME).
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal

DB_PATH = os.path.join(tempfile.gettempdir(), "printy_client_flow_verify.sqlite3")
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

from accounts.models import ClientInvite, User, UserProfile  # noqa: E402
from accounts.services.client_invites import (  # noqa: E402
    create_client_invite,
    link_client_identity,
)
from accounts.services.phone import normalize_phone, set_profile_phone  # noqa: E402
from api.dashboard_views import _resolve_or_create_partner_client  # noqa: E402
from clients.services import links as link_service  # noqa: E402
from clients.services.otp import request_otp, verify_otp  # noqa: E402
from clients.views import ClientLinkPaymentView  # noqa: E402
from jobs.managed_services import create_managed_job_from_accepted_quote  # noqa: E402
from jobs.models import ManagedJob  # noqa: E402
from jobs.views import JobTrackView  # noqa: E402
from payments.models import Payment  # noqa: E402
from payments.services import create_payment_for_quote  # noqa: E402
from pricing.services.platform_fee_policy import create_quote_financial_split  # noqa: E402
from quotes.acceptance import accept_quote_for_payment  # noqa: E402
from quotes.models import Quote, QuoteRequest  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402
from shops.models import Shop  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (" :: " + detail if detail else ""))


manager = User.objects.create(email="mgr@example.test", role=User.Role.BROKER, name="Manager")
other_manager = User.objects.create(email="mgr2@example.test", role=User.Role.BROKER, name="Other")
shop = Shop.objects.create(owner=manager, name="Flow Shop", slug="flow-shop")

# ------------------------------------------------- A. client created at send step
phone_a = "254711111111"
payload = _resolve_or_create_partner_client(
    partner_user=manager, client_name="Alice", client_phone="0711111111"
)
check("send step creates a client record", payload.get("status") == "invite_pending", str(payload.get("status")))
invite_a = ClientInvite.objects.filter(id=payload.get("invite_id")).first()
check("invite carries the manager as origin", invite_a is not None and invite_a.created_by_id == manager.id)
check("invite is for the normalised phone", invite_a is not None and invite_a.phone_normalized == phone_a)
check("email stays optional", invite_a is not None and not invite_a.email)

# first touch: a second manager cannot claim the same number away from the first
payload2 = _resolve_or_create_partner_client(
    partner_user=other_manager, client_name="Alice", client_phone="0711111111"
)
invite_a.refresh_from_db()
check("first touch keeps the original manager", invite_a.created_by_id == manager.id, str(invite_a.created_by_id))

# an existing client account is linked, never re-originated
alice = User.objects.create(email="alice@example.test", role=User.Role.CLIENT, name="Alice")
set_profile_phone(alice, "0711111111", verified=True)
linked = _resolve_or_create_partner_client(
    partner_user=other_manager, client_name="Alice", client_phone="0711111111"
)
check("existing client account is linked", linked.get("client_id") == alice.id and linked.get("status") == "linked")
check("existing client keeps no new origin", UserProfile.objects.get(user=alice).origin_manager_id is None)

manager_phone_user = UserProfile.objects.filter(user=manager).first()
set_profile_phone(manager, "0722222222", verified=True)
try:
    _resolve_or_create_partner_client(partner_user=other_manager, client_name="X", client_phone="0722222222")
    check("a manager's phone is refused as a client", False, "no error")
except ValueError:
    check("a manager's phone is refused as a client", True)

# ---------------------------------------------- B. signup links to the same record
phone_b = "254733333333"
invite_b, _ = create_client_invite(created_by=manager, name="Bob", phone="0733333333")
quote_request = QuoteRequest.objects.create(
    created_by=manager,
    assigned_manager=manager,
    created_by_manager=manager,
    customer_name="Bob",
    customer_phone=phone_b,
    status=QuoteRequest.QUOTED,
)
quote = Quote.objects.create(quote_request=quote_request, shop=shop, created_by=manager, total=Decimal("1600.00"), status=Quote.SENT)
create_quote_financial_split(quote=quote, production_cost=Decimal("1000.00"), broker_client_price=Decimal("1600.00"))
payment_before = create_payment_for_quote(quote=quote, payer=None, payer_phone=phone_b)
check("pre-signup payment has no payer", payment_before.payer_id is None)
# A separate request so the job reference below cannot collide with the job this quote
# creates later in part D.
job_link_request = QuoteRequest.objects.create(
    created_by=manager,
    assigned_manager=manager,
    created_by_manager=manager,
    customer_name="Bob",
    customer_phone=phone_b,
    status=QuoteRequest.QUOTED,
)
job_before = ManagedJob.objects.create(source_quote_request=job_link_request, client=None, status="payment_confirmed")

bob = User.objects.create(email="bob@example.test", role=User.Role.CLIENT, name="Bob")
set_profile_phone(bob, "0733333333", verified=False)
link_client_identity(bob, "0733333333")

bob_profile = UserProfile.objects.get(user=bob)
check("signup adopts the origin manager", bob_profile.origin_manager_id == manager.id)
invite_b.refresh_from_db()
check("signup claims the pending invite", invite_b.status == ClientInvite.Status.CLAIMED and invite_b.client_user_id == bob.id)
quote_request.refresh_from_db()
check("signup links the existing quote request", quote_request.on_behalf_of_id == bob.id)
payment_before.refresh_from_db()
check("signup links the existing payment", payment_before.payer_id == bob.id)
job_before.refresh_from_db()
check("signup links the existing job", job_before.client_id == bob.id)
check("no second client record was created", ClientInvite.objects.filter(phone_normalized=phone_b).count() == 1)

# a second account on the same phone must not hijack the record
intruder = User.objects.create(email="intruder@example.test", role=User.Role.CLIENT, name="Intruder")
set_profile_phone(intruder, "0733333333", verified=False)
link_client_identity(intruder, "0733333333")
check("a duplicate signup does not take over the record", UserProfile.objects.get(user=intruder).origin_manager_id is None)
bob_profile.refresh_from_db()
check("the original origin is untouched", bob_profile.origin_manager_id == manager.id)

# ----------------------------------------------------- C. anonymous accept + pay
quote.status = Quote.SENT
quote.accepted_at = None
quote.save(update_fields=["status", "accepted_at", "updated_at"])
try:
    accept_quote_for_payment(quote=quote, accepted_by=None, payer_phone=phone_b)
    check("anonymous accept needs the capability", False, "accepted without it")
except ValidationError:
    check("anonymous accept needs the capability", True)

admin_user = User.objects.create(email="admin@example.test", role=User.Role.SUPER_ADMIN, name="Admin", is_staff=True, is_superuser=True)
quote_accepted, payment_anon = accept_quote_for_payment(
    quote=quote, accepted_by=None, payer_phone=phone_b, authorized=True
)
check("capability accept accepts the quote", quote_accepted.status == Quote.ACCEPTED)
check("capability accept keeps the phone for STK", str(payment_anon.payer_phone or "").endswith("3333333"), str(payment_anon.payer_phone))

# A quote nobody has paid yet: the account-free payment must stay account-free.
phone_c = "254744444444"
anon_request = QuoteRequest.objects.create(
    created_by=manager,
    assigned_manager=manager,
    created_by_manager=manager,
    customer_name="Carol",
    customer_phone=phone_c,
    status=QuoteRequest.QUOTED,
)
anon_quote = Quote.objects.create(
    quote_request=anon_request, shop=shop, created_by=manager, total=Decimal("1600.00"), status=Quote.SENT
)
create_quote_financial_split(quote=anon_quote, production_cost=Decimal("1000.00"), broker_client_price=Decimal("1600.00"))
_accepted, payment_anon2 = accept_quote_for_payment(
    quote=anon_quote, accepted_by=None, payer_phone=phone_c, authorized=True
)
check("capability accept creates an account-free payment", payment_anon2.payer_id is None)
check("account-free payment carries the job phone", str(payment_anon2.payer_phone or "") == phone_c, str(payment_anon2.payer_phone))

# --------------------------------------------------- D. signed-out tracking payload
job = create_managed_job_from_accepted_quote(quote_request=quote_request, quote=quote_accepted, accepted_by=None)
payload_track = JobTrackView()._signed_out_payload(job)
blob = json.dumps(payload_track, default=str).lower()
check("tracking shows stages", bool(payload_track.get("stages")), str(len(payload_track.get("stages") or [])))
check("tracking shows who has the ball", bool(payload_track.get("who_has_the_ball")), str(payload_track.get("who_has_the_ball")))
check("tracking shows stage timestamps", any(stage.get("started_at") for stage in payload_track.get("stages") or []))
check("tracking keeps the job code", bool(payload_track.get("code")))
for forbidden in ("shop", "printy_fee", "production_cost", "manager_margin", "margin", "payout"):
    check(f"tracking hides {forbidden}", forbidden not in blob)

# ------------------------------------- E. the client link payment view (anonymous)
link = link_service.create_client_link(quote_request=quote_request, manager=manager)
challenge, code = request_otp(phone_normalized=normalize_phone(phone_b), quote_request=quote_request)
verified = verify_otp(phone_normalized=normalize_phone(phone_b), code=code, quote_request=quote_request)

import payments.services as payments_services  # noqa: E402


class _StubSTK:
    checkout_request_id = "ws_CO_verify"
    merchant_request_id = "mr_verify"
    status = "pending"


def _stub_push(*, payment, phone_number):
    payments_services.mark_payment_processing(
        payment, checkout_request_id=_StubSTK.checkout_request_id, merchant_request_id=_StubSTK.merchant_request_id
    )
    return _StubSTK()


original_push = payments_services.initiate_stk_push
payments_services.initiate_stk_push = _stub_push
try:
    factory = APIRequestFactory()
    request = factory.post(
        f"/api/client-portal/links/{link.token}/payment/",
        {"otp_token": verified.verified_token},
        format="json",
    )
    response = ClientLinkPaymentView.as_view()(request, token=link.token)
    body = response.data if hasattr(response, "data") else {}
    check("anonymous link payment returns 201", response.status_code == 201, f"{response.status_code} {body}")
    check("anonymous link payment pushed the STK", body.get("checkout_request_id") == "ws_CO_verify", str(body))
    check("anonymous link payment pays without an account", body.get("amount") is not None, str(body.get("amount")))
finally:
    payments_services.initiate_stk_push = original_push

# ------- F. the link itself accepts a SENT quote for a client with no account
phone_f = "254755555555"
sent_request = QuoteRequest.objects.create(
    created_by=manager,
    assigned_manager=manager,
    created_by_manager=manager,
    customer_name="Dee",
    customer_phone=phone_f,
    status=QuoteRequest.QUOTED,
)
sent_quote = Quote.objects.create(
    quote_request=sent_request, shop=shop, created_by=manager, total=Decimal("1600.00"), status=Quote.SENT
)
create_quote_financial_split(
    quote=sent_quote, production_cost=Decimal("1000.00"), broker_client_price=Decimal("1600.00")
)
sent_link = link_service.create_client_link(quote_request=sent_request, manager=manager)
_f_challenge, f_code = request_otp(phone_normalized=normalize_phone(phone_f), quote_request=sent_request)
f_verified = verify_otp(phone_normalized=normalize_phone(phone_f), code=f_code, quote_request=sent_request)

payments_services.initiate_stk_push = _stub_push
try:
    f_request = APIRequestFactory().post(
        f"/api/client-portal/links/{sent_link.token}/payment/",
        {"otp_token": f_verified.verified_token},
        format="json",
    )
    f_response = ClientLinkPaymentView.as_view()(f_request, token=sent_link.token)
    f_body = f_response.data if hasattr(f_response, "data") else {}
    check("the link accepts the sent quote", f_response.status_code == 201, f"{f_response.status_code} {f_body}")
    check("the link reports the acceptance", f_body.get("accepted_now") is True, str(f_body.get("accepted_now")))
    sent_quote.refresh_from_db()
    check("the sent quote is now accepted", sent_quote.status == Quote.ACCEPTED)
    check("no account paid it", Payment.objects.get(pk=f_body.get("payment_id")).payer_id is None)
finally:
    payments_services.initiate_stk_push = original_push

failed = [name for name, ok, _ in checks if not ok]
print("\n%d checks, %d failed" % (len(checks), len(failed)))
if failed:
    for name in failed:
        print("  FAILED:", name)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
