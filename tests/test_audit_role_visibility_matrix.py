"""Audit #3b: role visibility matrix.

Product rule (decided; see results.txt "Prompt 3 / STEP 0"):

- Signed-out visitors and clients find MANAGERS, never printers/shops. They
  never see shop name, slug, id, location, contact, shop-level costs or rate
  cards.
- Printers/shops are visible only to verified, active managers and
  super_admin. A printer owner sees only their own shop.
- Printers never see client identity or the total the client paid. Clients
  never see production costs.

One brokered job is seeded with deliberately distinctive values (shop name,
slug, phone, emails, address, client name/email/phone, client total,
production cost, shop payout). Every surface in the inventory is then called
as each role and the response body is scanned for those values and for
identity / cost keys. Shop-only surfaces must be denied to anonymous users,
clients, unverified managers and other printers.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from inventory.models import Machine, Paper
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob
from payments.models import Payment
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from quotes.models import Quote, QuoteFinancialSplit, QuoteRequest, QuoteShareLink
from shops.models import Shop

# -- distinctive values ------------------------------------------------------
SHOP_NAME = "Zebrafin Secret Press"
SHOP_SLUG = "zebrafin-secret-press"
SHOP_PHONE = "+254711987654"
SHOP_BIZ_EMAIL = "zebrafin-biz@shop.example"
SHOP_PUBLIC_EMAIL = "zebrafin-hello@shop.example"
SHOP_ADDRESS = "17 Hiddenmoss Lane"
SHOP_WHATSAPP = "+254711000999"
OTHER_SHOP_NAME = "Yakhorn Hidden Prints"
OTHER_SHOP_SLUG = "yakhorn-hidden-prints"

CLIENT_NAME = "Quillon Privateer"
CLIENT_EMAIL = "quillon-client@example.com"
CLIENT_PHONE = "+254722135790"

CLIENT_TOTAL = Decimal("9137.00")
PRODUCTION_COST = Decimal("5261.00")
SHOP_PAYOUT = Decimal("5011.00")

SHOP_IDENTITY_STRINGS = (
    SHOP_NAME,
    SHOP_SLUG,
    SHOP_PHONE,
    SHOP_PHONE.lstrip("+"),
    SHOP_BIZ_EMAIL,
    SHOP_PUBLIC_EMAIL,
    SHOP_ADDRESS,
    SHOP_WHATSAPP.lstrip("+"),
    OTHER_SHOP_NAME,
    OTHER_SHOP_SLUG,
)
SHOP_IDENTITY_KEYS = frozenset(
    {
        "shop_id",
        "shop_name",
        "shop_slug",
        "assigned_shop",
        "assigned_shop_id",
        "assigned_shop_name",
        "selected_shop",
        "selected_shop_id",
        "selected_shops",
        "selected_shop_ids",
        "production_shop",
        "production_shop_name",
        "shop_location",
        "shop_phone",
        "shop_email",
        "matched_shops",
    }
)
CLIENT_COST_KEYS = frozenset(
    {
        "production_cost",
        "production_base_price",
        "shop_payout",
        "printer_payout",
        "rate_card",
        "printing_rates",
        "paper_prices",
        "buying_price",
    }
)
CLIENT_COST_STRINGS = ("5261", "5011")

CLIENT_IDENTITY_STRINGS = (
    CLIENT_NAME,
    "Quillon",
    CLIENT_EMAIL,
    CLIENT_PHONE,
    CLIENT_PHONE.lstrip("+"),
)
CLIENT_TOTAL_STRINGS = ("9137",)
PRINTER_FORBIDDEN_KEYS = frozenset({"client_total", "client_price", "customer_email", "customer_phone"})

DENIED = {401, 403, 404}


def _walk(value, path="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{path}.{key}", str(key), child
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _amount_in(amount: str, text: str) -> bool:
    """Match a money amount as a whole number (not inside references/timestamps)."""
    return re.search(rf"(?<![\d\-]){re.escape(amount)}(?:\.\d+)?(?![\d])", text) is not None


def _non_empty(value):
    return value not in (None, "", [], {}, 0, False)


class RoleVisibilityMatrixTests(TestCase):
    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        PlatformFeePolicy.objects.update(is_active=False)
        cls.policy = PlatformFeePolicy.objects.create(name="Matrix policy", is_active=True)

        cls.admin = User.objects.create_superuser(email="matrix-admin@example.com", password="x")
        cls.client_user = User.objects.create_user(
            email=CLIENT_EMAIL, password="x", role=User.Role.CLIENT, name=CLIENT_NAME
        )
        UserProfile.objects.update_or_create(user=cls.client_user, defaults={"phone": CLIENT_PHONE})
        cls.manager = cls._manager("matrix-verified-mgr@example.com", verified=True)
        cls.unverified_manager = cls._manager("matrix-pending-mgr@example.com", verified=False)
        cls.rejected_manager = cls._manager("matrix-rejected-mgr@example.com", verified=False, active=False)

        cls.printer = User.objects.create_user(email="matrix-printer@example.com", password="x", role=User.Role.PRODUCTION)
        cls.other_printer = User.objects.create_user(
            email="matrix-printer-2@example.com", password="x", role=User.Role.PRODUCTION
        )
        cls.shop = cls._shop(cls.printer, SHOP_NAME, SHOP_SLUG)
        cls.shop.phone_number = SHOP_PHONE
        cls.shop.business_email = SHOP_BIZ_EMAIL
        cls.shop.public_email = SHOP_PUBLIC_EMAIL
        cls.shop.address_line = SHOP_ADDRESS
        cls.shop.public_whatsapp_number = SHOP_WHATSAPP
        cls.shop.latitude = Decimal("-1.2640")
        cls.shop.longitude = Decimal("36.8040")
        cls.shop.save()
        cls.other_shop = cls._shop(cls.other_printer, OTHER_SHOP_NAME, OTHER_SHOP_SLUG)

        cls.quote_request = QuoteRequest.objects.create(
            shop=cls.shop,
            created_by=cls.client_user,
            assigned_manager=cls.manager,
            customer_name=CLIENT_NAME,
            customer_email=CLIENT_EMAIL,
            customer_phone=CLIENT_PHONE,
            request_reference="QR-MATRIX01",
            request_snapshot={
                "selected_shop_ids": [cls.shop.id],
                "selected_shop": {"id": cls.shop.id, "name": SHOP_NAME, "slug": SHOP_SLUG},
                "production_cost": str(PRODUCTION_COST),
                "calculator_inputs": {"quantity": 100},
            },
        )
        cls.quote = Quote.objects.create(
            quote_request=cls.quote_request,
            shop=cls.shop,
            created_by=cls.manager,
            status=Quote.SENT,
            total=CLIENT_TOTAL,
            response_snapshot={
                "customer_pricing": {"final_client_price": str(CLIENT_TOTAL)},
                "internal_pricing_snapshot": {
                    "production_cost": str(PRODUCTION_COST),
                    "shop_payout": str(SHOP_PAYOUT),
                    "shop_name": SHOP_NAME,
                },
            },
        )
        QuoteFinancialSplit.objects.create(
            quote=cls.quote,
            policy_used=cls.policy,
            production_cost=PRODUCTION_COST,
            broker_client_price=CLIENT_TOTAL,
            gross_margin=CLIENT_TOTAL - PRODUCTION_COST,
            printer_side_fee=Decimal("250.00"),
            broker_margin_fee=Decimal("387.00"),
            printy_fee=Decimal("637.00"),
            shop_payout=SHOP_PAYOUT,
            manager_payout=Decimal("3489.00"),
            broker_payout=Decimal("3489.00"),
            client_total=CLIENT_TOTAL,
            max_allowed_client_price=Decimal("20000.00"),
            applied_markup_multiple=Decimal("1.7367"),
        )
        cls.job = ManagedJob.objects.create(
            title="Matrix job",
            source_quote_request=cls.quote_request,
            source_quote=cls.quote,
            client=cls.client_user,
            broker=cls.manager,
            assigned_shop=cls.shop,
            created_by=cls.manager,
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=CLIENT_TOTAL,
            printy_fee=Decimal("637.00"),
            broker_payout=Decimal("3489.00"),
            operational_snapshot={"production_cost": str(PRODUCTION_COST), "shop_name": SHOP_NAME},
        )
        cls.assignment = JobAssignment.objects.create(
            managed_job=cls.job,
            assigned_shop=cls.shop,
            source_quote=cls.quote,
            shop_payout=SHOP_PAYOUT,
            assignment_notes="Use supplied artwork.",
        )
        Payment.objects.create(
            quote=cls.quote,
            managed_job=cls.job,
            payer=cls.client_user,
            amount=CLIENT_TOTAL,
            status=Payment.STATUS_PAID,
            mpesa_receipt_number="MATRIX123",
        )
        cls.share = QuoteShareLink.objects.create(quote=cls.quote, token="matrix-share-token-0001")

    # -- fixtures ------------------------------------------------------------
    @staticmethod
    def _manager(email, *, verified, active=True):
        user = User.objects.create_user(email=email, password="x", role=User.Role.PARTNER, partner_profile_enabled=True)
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "manager_verified": verified,
                "manager_verified_at": timezone.now() if verified else None,
                "broker_profile_active": active,
                "public_display_name": email.split("@")[0],
            },
        )
        return user

    @staticmethod
    def _shop(owner, name, slug):
        shop = Shop.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            is_active=True,
            is_public=True,
            pricing_ready=True,
            public_match_ready=True,
            city="Nairobi",
            service_area="Westlands",
        )
        machine = Machine.objects.create(shop=shop, name=f"{name} Press", max_width_mm=320, max_height_mm=450, is_active=True)
        Paper.objects.create(
            shop=shop,
            name="300gsm Gloss",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug="cutting",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )
        return shop

    def setUp(self):
        cache.clear()

    # -- request helpers -------------------------------------------------------
    def _call(self, user, method, url, payload=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        if method == "GET":
            response = client.get(url, payload or {})
        else:
            response = client.post(url, payload or {}, format="json")
        try:
            body = response.json()
        except Exception:
            body = None
        return response.status_code, body

    def _role_users(self):
        return {
            "anonymous": None,
            "client": self.client_user,
            "unverified_manager": self.unverified_manager,
            "rejected_manager": self.rejected_manager,
            "verified_manager": self.manager,
            "printer_own": self.printer,
            "printer_other": self.other_printer,
            "super_admin": self.admin,
        }

    # -- scanners --------------------------------------------------------------
    @staticmethod
    def _identifies_a_shop(value):
        """True when a shop-ish key carries a real identifier.

        Masked labels ("Verified Print Partner", "Production option 1") are
        strings and are covered by the distinctive-value scan instead; here we
        flag ids and nested objects/lists that carry id/name/slug/location.
        """
        identity_keys = {"id", "shop_id", "latitude", "longitude", "lat", "lng", "phone_number", "business_email", "public_email"}
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        if isinstance(value, dict):
            return any(k in identity_keys and _non_empty(v) for k, v in value.items())
        if isinstance(value, list):
            return any(RoleVisibilityMatrixTests._identifies_a_shop(item) for item in value)
        return False

    @classmethod
    def _shop_identity_hits(cls, body):
        text = json.dumps(body, default=str)
        hits = [f"value:{s}" for s in SHOP_IDENTITY_STRINGS if s in text]
        for path, key, value in _walk(body):
            if "field_errors" in path or ".errors" in path:
                continue
            if (key in SHOP_IDENTITY_KEYS or key == "shop") and cls._identifies_a_shop(value):
                hits.append(f"key:{path}")
        return hits

    @staticmethod
    def _client_cost_hits(body):
        text = json.dumps(body, default=str)
        hits = [f"value:{s}" for s in CLIENT_COST_STRINGS if _amount_in(s, text)]
        for path, key, value in _walk(body):
            if key in CLIENT_COST_KEYS and _non_empty(value):
                hits.append(f"key:{path}")
        return hits

    @staticmethod
    def _printer_hits(body):
        text = json.dumps(body, default=str)
        hits = [f"value:{s}" for s in CLIENT_IDENTITY_STRINGS if s in text]
        hits += [f"value:{s}" for s in CLIENT_TOTAL_STRINGS if _amount_in(s, text)]
        for path, key, value in _walk(body):
            if key in PRINTER_FORBIDDEN_KEYS and _non_empty(value):
                hits.append(f"key:{path}")
        return hits

    # -- surfaces ----------------------------------------------------------------
    def _calc_payload(self):
        return {
            "product_type": "business_card",
            "job_type": "business_cards",
            "quantity": 100,
            "width_mm": 85,
            "height_mm": 55,
            "paper_gsm": 300,
            "paper_type": "gloss",
            "print_sides": "SIMPLEX",
            "color_mode": "COLOR",
        }

    def _shop_surfaces(self, shop):
        """Printer/shop data. Only verified managers, super_admin, own printer."""
        slug = shop.slug
        return [
            ("GET", "/api/public/shops/", None, False),
            ("GET", f"/api/public/shops/{slug}/", None, True),
            ("GET", f"/api/public/shops/{slug}/catalog/", None, True),
            ("GET", f"/api/public/shops/{slug}/custom-options/", None, True),
            ("GET", f"/api/shops/{slug}/rate-card/", None, True),
            ("GET", f"/api/shops/{slug}/rate-card-for-calculator/", None, True),
            ("GET", "/api/shops/nearby/", {"lat": "-1.2640", "lng": "36.8040", "radius": "50"}, False),
            ("POST", f"/api/public/shops/{slug}/calculator-preview/", self._calc_payload(), True),
            ("POST", f"/api/public/shops/{slug}/quote-preview/", self._calc_payload(), True),
        ]

    def test_shop_surfaces_follow_the_rule_for_every_role(self):
        problems = []
        roles = self._role_users()
        for method, url, payload, shop_scoped in self._shop_surfaces(self.shop):
            for role, user in roles.items():
                status_code, body = self._call(user, method, url, payload)
                if role in {"anonymous", "client", "unverified_manager", "rejected_manager"}:
                    expected_denied = True
                elif role == "printer_other":
                    expected_denied = True
                elif role == "printer_own":
                    expected_denied = not shop_scoped
                else:
                    expected_denied = False
                if expected_denied:
                    if status_code not in DENIED:
                        problems.append(f"{role} {method} {url}: expected denial, got {status_code}")
                    elif body is not None and self._shop_identity_hits(body):
                        problems.append(f"{role} {method} {url}: denial body leaks {self._shop_identity_hits(body)}")
                else:
                    if status_code >= 500 or status_code in DENIED:
                        problems.append(f"{role} {method} {url}: expected access, got {status_code}")
        self.assertEqual(problems, [], "\n".join(problems))

    def _client_surfaces(self):
        job, qr, quote = self.job, self.quote_request, self.quote
        return [
            ("GET", "/api/dashboard/client-home/", None),
            ("GET", "/api/dashboard/client/quotes/", None),
            ("GET", f"/api/dashboard/client/quotes/{quote.id}/", None),
            ("GET", "/api/dashboard/client/jobs/", None),
            ("GET", f"/api/dashboard/client/jobs/{job.id}/", None),
            ("GET", "/api/dashboard/client/tracked-jobs/", None),
            ("GET", f"/api/dashboard/client/tracked-jobs/{job.id}/", None),
            ("GET", "/api/dashboard/client/payments/", None),
            ("GET", "/api/managed-jobs/", None),
            ("GET", f"/api/managed-jobs/{job.id}/", None),
            ("GET", f"/api/managed-jobs/{job.id}/card/", None),
            ("GET", f"/api/managed-jobs/{job.id}/events/", None),
            ("GET", f"/api/managed-jobs/{job.id}/payments/", None),
            ("GET", f"/api/track/{job.managed_reference}/", None),
            ("GET", f"/api/track/{qr.request_reference}/", None),
            ("GET", f"/api/public/managed-jobs/track/{job.tracking_token}/", None),
            ("GET", "/api/quote-requests/", None),
            ("GET", f"/api/quote-requests/{qr.id}/", None),
            ("GET", f"/api/client/requests/{qr.id}/", None),
            ("GET", "/api/client/responses/", None),
            ("GET", "/api/calculator/buyer-quotes/", None),
            ("GET", "/api/client/messages/", None),
            ("GET", "/api/me/notifications/", None),
            ("GET", f"/api/share/{self.share.token}/", None),
            ("GET", f"/api/quote-requests/{qr.id}/brief/", None),
            ("GET", f"/api/quote-requests/{qr.id}/whatsapp-handoff/", None),
            ("GET", "/api/dashboard/counts/", None),
            ("GET", "/api/dashboard/needs-my-action/", None),
            ("GET", "/api/buyer/locked-managers/", None),
            ("GET", "/api/intake/recommended-managers/", {"product_type": "business_card", "quantity": 100}),
        ]

    def _public_surfaces(self):
        """Anonymous + client discovery / calculator surfaces (must stay usable)."""
        calc = self._calc_payload()
        return [
            ("POST", "/api/public/recommended-managers/", calc, False),
            ("GET", "/api/public/print-managers/recommended/", {"product_type": "business_card", "quantity": 100}, True),
            ("GET", "/api/public/print-managers/featured/", None, True),
            ("GET", "/api/calculator/config/", None, True),
            ("POST", "/api/calculator/public-preview/", calc, False),
            ("POST", "/api/public/match-shops/", calc, False),
            ("POST", "/api/public/calculator/preview/", calc, False),
            ("GET", "/api/public/products/", None, True),
            ("GET", "/api/public/price-range/", {"product_type": "business_card", "quantity": 100}, False),
            ("GET", "/api/seo/products/", None, True),
            ("GET", f"/api/public/managed-jobs/track/{self.job.tracking_token}/", None, True),
            ("GET", f"/api/track/{self.job.managed_reference}/", {"phone": CLIENT_PHONE}, True),
            ("GET", f"/api/share/{self.share.token}/", None, True),
        ]

    def test_client_surfaces_never_show_shop_identity_or_production_cost(self):
        problems = []
        for method, url, payload in self._client_surfaces():
            status_code, body = self._call(self.client_user, method, url, payload)
            if status_code >= 500:
                problems.append(f"client {method} {url}: {status_code}")
                continue
            if body is None:
                continue
            shop_hits = self._shop_identity_hits(body)
            cost_hits = self._client_cost_hits(body)
            if shop_hits:
                problems.append(f"client {method} {url} [{status_code}] shop identity: {shop_hits}")
            if cost_hits:
                problems.append(f"client {method} {url} [{status_code}] production cost: {cost_hits}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_public_surfaces_are_usable_and_never_show_shops_or_costs(self):
        problems = []
        for role, user in (("anonymous", None), ("client", self.client_user)):
            for method, url, payload, must_succeed in self._public_surfaces():
                status_code, body = self._call(user, method, url, payload)
                if status_code >= 500 or (must_succeed and status_code != 200):
                    problems.append(f"{role} {method} {url}: status {status_code}")
                if body is None:
                    continue
                shop_hits = self._shop_identity_hits(body)
                cost_hits = self._client_cost_hits(body)
                if shop_hits:
                    problems.append(f"{role} {method} {url} [{status_code}] shop identity: {shop_hits}")
                if cost_hits:
                    problems.append(f"{role} {method} {url} [{status_code}] production cost: {cost_hits}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_verified_manager_discovery_still_lists_managers(self):
        status_code, body = self._call(None, "GET", "/api/public/print-managers/featured/")
        self.assertEqual(status_code, 200, body)

    def _printer_surfaces(self):
        job = self.job
        return [
            ("GET", "/api/dashboard/production-home/", None),
            ("GET", "/api/dashboard/production/jobs/", None),
            ("GET", f"/api/dashboard/production/jobs/{job.id}/", None),
            ("GET", f"/api/dashboard/printshop/jobs/{job.id}/breakdown/", None),
            ("GET", "/api/shop/assignments/", None),
            ("GET", "/api/managed-jobs/", None),
            ("GET", f"/api/managed-jobs/{job.id}/", None),
            ("GET", f"/api/managed-jobs/{job.id}/card/", None),
            ("GET", f"/api/managed-jobs/{job.id}/events/", None),
            ("GET", f"/api/track/{job.managed_reference}/", None),
            ("GET", "/api/shop/messages/", None),
            ("GET", "/api/production-orders/", None),
            ("GET", "/api/dashboard/production/payments/", None),
            ("GET", "/api/me/notifications/", None),
            ("GET", "/api/dashboard/counts/", None),
            ("GET", "/api/dashboard/needs-my-action/", None),
            ("GET", "/api/dashboard/shop-home/", None),
            ("GET", f"/api/shops/{SHOP_SLUG}/incoming-requests/", None),
            ("GET", f"/api/shops/{SHOP_SLUG}/incoming-requests/{self.quote_request.id}/", None),
            ("GET", f"/api/shops/{SHOP_SLUG}/incoming-requests/{self.quote_request.id}/brief/", None),
            ("GET", f"/api/shops/{SHOP_SLUG}/incoming-requests/{self.quote_request.id}/whatsapp-handoff/", None),
            ("GET", f"/api/shops/{SHOP_SLUG}/quotes/", None),
        ]

    def test_printer_surfaces_never_show_client_identity_or_client_total(self):
        problems = []
        for method, url, payload in self._printer_surfaces():
            status_code, body = self._call(self.printer, method, url, payload)
            if status_code >= 500:
                problems.append(f"printer {method} {url}: {status_code}")
                continue
            if body is None:
                continue
            hits = self._printer_hits(body)
            if hits:
                problems.append(f"printer {method} {url} [{status_code}]: {hits}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_other_printer_sees_nothing_of_this_job(self):
        problems = []
        for method, url, payload in self._printer_surfaces():
            status_code, body = self._call(self.other_printer, method, url, payload)
            if body is None:
                continue
            text = json.dumps(body, default=str)
            for needle in (SHOP_NAME, SHOP_SLUG, self.job.managed_reference, "Matrix job"):
                if needle in text:
                    problems.append(f"other printer {method} {url} [{status_code}]: {needle}")
        self.assertEqual(problems, [], "\n".join(problems))

    # -- payouts (Prompt 7 PART 2) -------------------------------------------------
    def test_payout_surfaces_follow_the_rule_for_every_role(self):
        """Shop owner sees its own payout, manager theirs, client none; the
        payout admin API is super_admin only and never changes anything for
        anyone else."""
        from jobs.models import ManagedJobPayout as P

        shop_row = P.objects.create(managed_job=self.job, recipient_role=P.RECIPIENT_ROLE_SHOP,
                                    recipient=self.printer, amount=SHOP_PAYOUT, status=P.STATUS_NEEDS_REVIEW,
                                    waiting_reason="legacy_no_handover_code")
        manager_row = P.objects.create(managed_job=self.job, recipient_role=P.RECIPIENT_ROLE_MANAGER,
                                       recipient=self.manager, amount=Decimal("3489.00"), status=P.STATUS_PENDING)
        problems = []
        expected_mine = {
            "anonymous": (DENIED, None),
            "client": ({403}, None),
            "unverified_manager": ({200, 403}, []),
            "rejected_manager": ({200, 403}, []),
            "verified_manager": ({200}, ["manager"]),
            "printer_own": ({200}, ["shop"]),
            "printer_other": ({200, 403}, []),
            "super_admin": ({200}, []),
        }
        for role, user in self._role_users().items():
            allowed, roles_seen = expected_mine[role]
            status_code, body = self._call(user, "GET", "/api/payouts/mine/")
            if status_code not in allowed:
                problems.append(f"{role} GET /api/payouts/mine/: {status_code}")
                continue
            if roles_seen is not None and status_code == 200:
                seen = sorted(row["recipient_role"] for row in body["results"])
                if seen != sorted(roles_seen):
                    problems.append(f"{role} /api/payouts/mine/ saw {seen}")
            if role == "printer_own" and body:
                hits = self._printer_hits(body)
                if hits:
                    problems.append(f"printer /api/payouts/mine/: {hits}")
            if role == "client" and body:
                hits = self._client_cost_hits(body)
                if hits:
                    problems.append(f"client /api/payouts/mine/: {hits}")

        admin_surfaces = [
            ("GET", "/api/admin/payouts/", None),
            ("GET", f"/api/admin/jobs/{self.job.id}/money-summary/", None),
            ("POST", f"/api/admin/payouts/{shop_row.id}/approve/", {"confirm_amount": str(SHOP_PAYOUT), "reason": "x"}),
            ("POST", f"/api/admin/payouts/{manager_row.id}/retry/", {"reason": "x"}),
        ]
        for role, user in self._role_users().items():
            if role == "super_admin":
                continue
            for method, url, payload in admin_surfaces:
                status_code, _ = self._call(user, method, url, payload)
                if status_code not in DENIED:
                    problems.append(f"{role} {method} {url}: {status_code}")
        for method, url, payload in admin_surfaces[:2]:
            status_code, _ = self._call(self.admin, method, url, payload)
            if status_code != 200:
                problems.append(f"super_admin {method} {url}: {status_code}")
        shop_row.refresh_from_db()
        manager_row.refresh_from_db()
        self.assertEqual((shop_row.status, manager_row.status), (P.STATUS_NEEDS_REVIEW, P.STATUS_PENDING))
        self.assertEqual(problems, [], "\n".join(problems))

    # -- emails ------------------------------------------------------------------
    def _email_text(self, *, message_type, recipient_role):
        from quotes.messaging import _prepared_email_payload_for_message
        from quotes.models import QuoteRequestMessage

        message = QuoteRequestMessage(
            quote_request=self.quote_request,
            quote=self.quote,
            shop=self.shop,
            message_type=message_type,
            recipient_role=recipient_role,
            subject="Printy update",
            body="Printy update",
        )
        prepared = _prepared_email_payload_for_message(message)
        return " ".join(
            [prepared["subject"], prepared["body"], prepared["html_body"], json.dumps(prepared["context"], default=str)]
        )

    def test_shop_owner_emails_never_carry_client_identity(self):
        from quotes.models import QuoteRequestMessage

        for message_type in (
            QuoteRequestMessage.MessageType.QUOTE_REQUEST_CREATED,
            QuoteRequestMessage.MessageType.QUOTE_ACCEPTED,
        ):
            text = self._email_text(message_type=message_type, recipient_role=QuoteRequestMessage.RecipientRole.SHOP_OWNER)
            for needle in CLIENT_IDENTITY_STRINGS:
                self.assertNotIn(needle, text, f"{message_type} email to shop owner leaks {needle}")

    def test_client_emails_never_carry_shop_identity(self):
        from quotes.models import QuoteRequestMessage

        text = self._email_text(
            message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
        )
        for needle in SHOP_IDENTITY_STRINGS:
            self.assertNotIn(needle, text, f"client email leaks {needle}")

    # -- in-app notifications ----------------------------------------------------
    def test_printer_notifications_never_carry_client_identity(self):
        from quotes.choices import QuoteStatus

        draft = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            customer_name=CLIENT_NAME,
            customer_email=CLIENT_EMAIL,
            customer_phone=CLIENT_PHONE,
            status=QuoteStatus.DRAFT,
        )
        status_code, body = self._call(self.client_user, "POST", f"/api/quote-requests/{draft.id}/submit/")
        self.assertIn(status_code, {200, 201}, body)
        draft.refresh_from_db()
        draft.status = QuoteStatus.AWAITING_CLIENT_REPLY
        draft.save(update_fields=["status"])
        status_code, body = self._call(
            self.client_user, "POST", f"/api/quote-requests/{draft.id}/reply/", {"body": "Here are the details."}
        )
        self.assertIn(status_code, {200, 201}, body)

        status_code, body = self._call(self.printer, "GET", "/api/me/notifications/")
        self.assertEqual(status_code, 200, body)
        text = json.dumps(body, default=str)
        self.assertIn(f"#{draft.id}", text, "fixture should have produced printer notifications")
        hits = [needle for needle in CLIENT_IDENTITY_STRINGS if needle in text]
        self.assertEqual(hits, [], f"printer notifications leak client identity: {hits}")

    # -- manager-only printer roster ---------------------------------------------
    def test_printer_roster_is_for_verified_managers_only(self):
        source_payload = {
            **self._calc_payload(),
            "calculator_context": "manager_dashboard",
            "intent": "source_production",
        }
        surfaces = [
            ("GET", "/api/dashboard/partner/production-shops/", None),
            ("POST", "/api/partner/production-matches/", source_payload),
        ]
        problems = []
        for method, url, payload in surfaces:
            for role in ("anonymous", "client", "unverified_manager", "rejected_manager", "printer_own", "printer_other"):
                status_code, body = self._call(self._role_users()[role], method, url, payload)
                if status_code not in DENIED:
                    problems.append(f"{role} {method} {url}: expected denial, got {status_code}")
            status_code, body = self._call(self.manager, method, url, payload)
            if status_code != 200:
                problems.append(f"verified_manager {method} {url}: expected 200, got {status_code} {body}")
        status_code, body = self._call(self.manager, "GET", "/api/dashboard/partner/production-shops/")
        self.assertIn(SHOP_NAME, json.dumps(body, default=str), "verified managers still see the roster")
        self.assertEqual(problems, [], "\n".join(problems))
