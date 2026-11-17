"""Phase A end-to-end job simulation (real HTTP endpoints only).

Prompt: END-TO-END JOB SIMULATION (BACKEND, printy_api).

Every step drives the real API with an authenticated APIClient and records a
PASS/FAIL/BLOCKED line. No app code is modified here; all fixtures are created
inside the test. M-Pesa is stubbed but the callback is POSTed to the real
callback endpoint exactly as Daraja would call it.

Run:
    python manage.py test tests.test_e2e_job_simulation -v 2

The test prints a machine-readable report:

    SIM_RESULT|<step>|<PASS/FAIL/BLOCKED>|<evidence>
"""

from __future__ import annotations

import json
from datetime import time
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from jobs.choices import ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob
from payments.models import Payment
from pricing.models import PlatformFeePolicy
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from shops.models import Shop


def _json_safe(value):
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


class E2EJobSimulation(TestCase):
    """S1-S14. Failures are recorded instead of aborting the whole run."""

    maxDiff = None

    def setUp(self):
        self.api = APIClient()
        self.results: list[tuple[str, str, str]] = []

        self.buyer = User.objects.create_user(
            email="asha-sim@example.com", password="pass12345", role=User.Role.CLIENT,
            name="Asha Test",
        )
        self.manager = User.objects.create_user(
            email="maya-sim@example.com", password="pass12345", role=User.Role.PARTNER,
            name="Maya Test",
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
            partner_profile_enabled=True,
        )
        self.other_manager = User.objects.create_user(
            email="other-manager-sim@example.com", password="pass12345", role=User.Role.PARTNER,
            name="Other Manager",
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(
            email="kito-sim@example.com", password="pass12345", role=User.Role.PRODUCTION,
            name="Kito Owner",
        )
        self.shop = Shop.objects.create(
            name="Kito Press", slug="kito-press", owner=self.printer, is_active=True,
            is_public=True, supports_custom_requests=True, opening_time=time(8, 0),
            closing_time=time(18, 0), city="Nairobi",
        )
        self.admin = User.objects.create_superuser(
            email="ops-sim@example.com", password="pass12345", name="Printy Ops",
        )
        self.policy = PlatformFeePolicy.objects.create(
            name="Simulation policy", is_active=True,
            printer_fee_rate=Decimal("0.0500"),
            broker_margin_fee_rate=Decimal("0.1500"),
            add_platform_fee_on_top=False,
        )
        self.spec = {
            "product_type": "business_card",
            "quantity": 300,
            "finished_size": "85x55mm",
            "print_sides": "DUPLEX",
            "color_mode": "COLOR",
            "requested_paper_category": "artcard",
            "requested_gsm": 300,
            "lamination": "matte-lamination",
        }

    # ------------------------------------------------------------------ util

    def _record(self, step: str, passed: bool | None, evidence: str):
        status = "BLOCKED" if passed is None else ("PASS" if passed else "FAIL")
        self.results.append((step, status, evidence))
        print(f"SIM_RESULT|{step}|{status}|{evidence}", flush=True)

    def _dump(self, response) -> str:
        try:
            return json.dumps(response.json())
        except Exception:
            return (response.content or b"").decode("utf-8", "replace")[:400]

    def _assert(self, step: str, condition: bool, evidence: str) -> bool:
        self._record(step, condition, evidence)
        return bool(condition)

    def _contains_forbidden(self, payload, forbidden) -> list[str]:
        found: list[str] = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in forbidden:
                        found.append(key)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return found

    def _create_and_send_request(self, *, manager=None, spec=None, artwork=False) -> QuoteRequest | None:
        self.api.force_authenticate(self.buyer)
        body = {"calculator_inputs_snapshot": spec or self.spec, "title": "Simulation job"}
        created = self.api.post("/api/calculator/drafts/", body, format="json")
        if created.status_code != 201:
            return None
        draft_id = created.json()["id"]
        send_body = {
            "selected_manager_id": (manager or self.manager).id,
            "manager_selection_mode": "client_selected",
        }
        if artwork:
            upload = self.api.post(
                "/api/calculator/artwork-upload/",
                {"file": SimpleUploadedFile("sim-artwork.pdf", b"%PDF-1.4 sim artwork")},
                format="multipart",
            )
            if upload.status_code == 201:
                send_body["artwork_token"] = upload.json().get("token", "")
                send_body["artwork_filename"] = "sim-artwork.pdf"
        sent = self.api.post(f"/api/calculator/drafts/{draft_id}/send/", send_body, format="json")
        if sent.status_code not in (200, 201):
            return None
        data = sent.json()
        request_id = data[0]["id"] if isinstance(data, list) and data else data.get("id")
        return QuoteRequest.objects.filter(pk=request_id).first()

    def _prepare(self, quote_request: QuoteRequest, *, markup_pct="75.00", shop=None):
        shop = shop or self.shop
        pricing_snapshot = {
            "selected_shops": [
                {
                    "id": shop.id,
                    "slug": shop.slug,
                    "name": shop.name,
                    "totals": {"shop_total": "1000.00", "grand_total": "1000.00"},
                    "preview": {
                        "totals": {"shop_total": "1000.00", "grand_total": "1000.00", "subtotal": "1000.00"},
                        "breakdown": {
                            "imposition": {"good_sheets": 10},
                            "paper": {"total": "400.00", "quantity": 10},
                            "printing": {"total": "600.00", "quantity": 10},
                        },
                    },
                }
            ]
        }
        return self.api.post(
            f"/api/dashboard/partner/quotes/{quote_request.id}/prepare/",
            {"shop": shop.id, "pricing_snapshot": pricing_snapshot, "markup_pct": markup_pct},
            format="json",
        )

    def _success_callback(self, stk, amount: str, receipt: str = "SIMRCPT1"):
        return {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": stk.checkout_request_id,
                    "MerchantRequestID": stk.merchant_request_id,
                    "ResultCode": 0,
                    "ResultDesc": "Success",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": amount},
                            {"Name": "MpesaReceiptNumber", "Value": receipt},
                        ]
                    },
                }
            }
        }

    # ------------------------------------------------------------------ run

    @override_settings(MPESA_ENVIRONMENT="test", MPESA_FORCE_STUB=True)
    def test_e2e_simulation(self):
        def run(step, fn):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - Phase A records, never aborts
                self._record(step, False, f"exception {type(exc).__name__}: {exc}")
                return None

        try:
            run("S1", self._s1_quote_request)
            run("S2", self._s2_manager_selection)
            run("S3", self._s3_manager_receives)
            run("S3b", self._s3b_printer_visibility)
            prepared = run("S4", self._s4_prepare_and_send) or (None, None)
            quote_request, quote = prepared
            if quote_request is not None:
                quote = run("S4b", lambda: self._s4b_price_change(quote_request, quote)) or quote
                run("S5", lambda: self._s5_buyer_decision(quote_request, quote))
            else:
                self._record("S4b", None, "blocked: no prepared quote")
                self._record("S5", None, "blocked: no prepared quote")
            paid = run("S6", lambda: self._s6_payment(quote)) or (None, None)
            managed_job, payment = paid
            if managed_job is not None:
                run("S7", lambda: self._s7_artwork(managed_job))
                run("S8", lambda: self._s8_artwork_check(managed_job))
                run("S9", lambda: self._s9_production(managed_job))
                run("S10", lambda: self._s10_dispatch_delivery(managed_job))
                run("S11", lambda: self._s11_payout(managed_job, payment))
                run("S12", lambda: self._s12_tracking(managed_job))
            else:
                for step in ("S7", "S8", "S9", "S10", "S11", "S12"):
                    self._record(step, None, "blocked: no managed job (payment step failed)")
            run("S13", self._s13_manager_added_client)
            run("S14", self._s14_role_matrix)
        finally:
            print("SIM_TABLE_START", flush=True)
            for step, status, evidence in self.results:
                print(f"{step}\t{status}\t{evidence}", flush=True)
            print("SIM_TABLE_END", flush=True)

    # ------------------------------------------------------------- S1 / S2

    def _s1_quote_request(self):
        self.api.force_authenticate(self.buyer)
        created = self.api.post(
            "/api/calculator/drafts/",
            {"calculator_inputs_snapshot": self.spec, "title": "Job A"},
            format="json",
        )
        if created.status_code != 201:
            self._record("S1", False, f"draft create {created.status_code}: {self._dump(created)}")
            return
        draft_id = created.json()["id"]
        sent = self.api.post(
            f"/api/calculator/drafts/{draft_id}/send/",
            {"selected_manager_id": self.manager.id, "manager_selection_mode": "client_selected"},
            format="json",
        )
        quote_request = None
        if sent.status_code in (200, 201):
            data = sent.json()
            request_id = data[0]["id"] if isinstance(data, list) and data else data.get("id")
            quote_request = QuoteRequest.objects.filter(pk=request_id).first()
        ok = bool(
            quote_request
            and quote_request.assigned_manager_id == self.manager.id
            and quote_request.status == QuoteStatus.SUBMITTED
        )
        self._record(
            "S1",
            ok,
            f"draft={created.status_code} send={sent.status_code} "
            f"request={getattr(quote_request, 'id', None)} status={getattr(quote_request, 'status', None)}",
        )
        # Buyer payload must not leak printer/economics fields.
        if quote_request:
            detail = self.api.get(f"/api/client/requests/{quote_request.id}/")
            forbidden = [
                "shop", "shop_name", "assigned_shop_name", "production_cost", "printer_price",
                "markup", "manager_margin", "printy_fee", "imposition", "sheets",
            ]
            leaks = self._contains_forbidden(detail.json() if detail.status_code == 200 else {}, forbidden)
            self._record("S1.privacy", not leaks, f"detail={detail.status_code} leaked={leaks or 'none'}")
        # No-artwork path must still be accepted.
        second = self._create_and_send_request(spec={**self.spec, "quantity": 200, "finished_size": "A6"})
        self._record("S1.no_artwork", second is not None, f"request={getattr(second, 'id', None)}")
        # Non-buyers cannot use the buyer draft endpoint.
        self.api.force_authenticate(self.manager)
        forbidden_manager = self.api.post(
            "/api/calculator/drafts/", {"calculator_inputs_snapshot": self.spec}, format="json",
        )
        self.api.force_authenticate(self.printer)
        forbidden_printer = self.api.post(
            "/api/calculator/drafts/", {"calculator_inputs_snapshot": self.spec}, format="json",
        )
        self._record(
            "S1.roles",
            forbidden_manager.status_code == 403 and forbidden_printer.status_code == 403,
            f"manager={forbidden_manager.status_code} printer={forbidden_printer.status_code}",
        )

    def _s2_manager_selection(self):
        # The buyer-chosen manager is persisted on the request.
        request = QuoteRequest.objects.filter(assigned_manager=self.manager).order_by("-id").first()
        self._record(
            "S2.persisted",
            bool(request and request.assigned_manager_id == self.manager.id),
            f"request={getattr(request, 'id', None)} assigned_manager={getattr(request, 'assigned_manager_id', None)}",
        )
        # Guest draft claim: a signed-out draft is claimed by the signed-in buyer
        # instead of creating a second draft (the server side of "survives signup").
        self.api.force_authenticate(None)
        guest = self.api.post(
            "/api/calculator/guest-drafts/",
            {"session_key": "sim-session-1", "calculator_inputs_snapshot": {**self.spec, "quantity": 111}},
            format="json",
        )
        self.api.force_authenticate(self.buyer)
        claimed = self.api.post(
            "/api/calculator/drafts/",
            {
                "calculator_inputs_snapshot": {**self.spec, "quantity": 111},
                "session_key": "sim-session-1",
            },
            format="json",
        )
        claimed_id = claimed.json().get("id") if claimed.status_code == 201 else None
        guest_id = guest.json().get("id") if guest.status_code in (200, 201) else None
        self._record(
            "S2.guest_carryover",
            bool(claimed_id and guest_id and claimed_id == guest_id),
            f"guest={guest.status_code}/{guest_id} claim={claimed.status_code}/{claimed_id}",
        )
        # Recommendation ranking: both managers are eligible; ordering is server-owned.
        rec = self.api.get("/api/intake/recommended-managers/?product_type=business_card&quantity=300")
        ranked = rec.json().get("results", []) if rec.status_code == 200 else []
        self._record(
            "S2.recommendations",
            rec.status_code == 200 and isinstance(ranked, list),
            f"status={rec.status_code} count={len(ranked)}",
        )

    # ------------------------------------------------------------- S3 / S3b

    def _s3_manager_receives(self):
        request = QuoteRequest.objects.filter(assigned_manager=self.manager).order_by("-id").first()
        self.api.force_authenticate(self.manager)
        listing = self.api.get("/api/dashboard/partner/quotes/")
        rows = listing.json().get("results", []) if listing.status_code == 200 else []
        visible = any(row.get("id") == request.id for row in rows) if request else False
        self._record("S3.visible", visible, f"status={listing.status_code} rows={len(rows)} request={getattr(request,'id',None)}")
        self.api.force_authenticate(self.other_manager)
        other = self.api.get(f"/api/dashboard/partner/quotes/{request.id}/") if request else None
        self._record("S3.other_manager_denied", other is not None and other.status_code == 404, f"status={getattr(other,'status_code',None)}")
        self.api.force_authenticate(self.manager)
        options = self.api.post(
            f"/api/dashboard/partner/quotes/{request.id}/shop-options/",
            {"quantity": self.spec["quantity"], "finished_size": self.spec["finished_size"]},
            format="json",
        )
        shops = (options.json().get("shops") or options.json().get("results") or []) if options.status_code == 200 else []
        self._record(
            "S3.shop_options",
            options.status_code == 200,
            f"status={options.status_code} shops={len(shops)} body={self._dump(options)[:180]}",
        )

    def _s3b_printer_visibility(self):
        request = QuoteRequest.objects.filter(assigned_manager=self.manager).order_by("-id").first()
        self.api.force_authenticate(self.printer)
        # Before dispatch there is no production job for the printer.
        jobs = self.api.get("/api/dashboard/production/jobs/")
        rows = jobs.json().get("results", []) if jobs.status_code == 200 else []
        self._record("S3b.no_pre_dispatch_job", jobs.status_code == 200 and rows == [], f"status={jobs.status_code} rows={len(rows)}")
        detail = self.api.get(f"/api/client/requests/{request.id}/") if request else None
        self._record("S3b.buyer_endpoint_denied", detail is not None and detail.status_code in (403, 404), f"status={getattr(detail,'status_code',None)}")

    # ------------------------------------------------------------- S4 / S4b

    def _s4_prepare_and_send(self):
        request = QuoteRequest.objects.filter(assigned_manager=self.manager).order_by("-id").first()
        if not request:
            self._record("S4", False, "no quote request to prepare")
            return None, None
        self.api.force_authenticate(self.manager)
        prepared = self._prepare(request)
        if prepared.status_code != 201:
            self._record("S4.prepare", False, f"status={prepared.status_code} body={self._dump(prepared)[:300]}")
            return request, None
        data = prepared.json()
        quote_id = data.get("quote", {}).get("id")
        self._record(
            "S4.prepare",
            bool(quote_id),
            f"status={prepared.status_code} quote={quote_id} keys={sorted(data.keys())[:8]}",
        )
        sent = self.api.post(
            f"/api/dashboard/partner/quotes/{request.id}/send-to-client/",
            {"broker_margin_type": "percent", "broker_margin_value": "75", "note": "Simulation quote"},
            format="json",
        )
        self._record("S4.send", sent.status_code == 200, f"status={sent.status_code} body={self._dump(sent)[:200]}")
        # Locked: second send must not create a second email/outbox row.
        from quotes.models import EmailOutbox
        before = EmailOutbox.objects.count()
        again = self.api.post(
            f"/api/dashboard/partner/quotes/{request.id}/send-to-client/",
            {"broker_margin_type": "percent", "broker_margin_value": "75"},
            format="json",
        )
        after = EmailOutbox.objects.count()
        self._record(
            "S4.locked",
            again.status_code in (400, 409, 200) and after == before,
            f"second={again.status_code} outbox_before={before} outbox_after={after}",
        )
        from quotes.models import Quote
        quote = Quote.objects.filter(pk=quote_id).first()
        return request, quote

    def _s4b_price_change(self, request, quote):
        if not quote:
            self._record("S4b", None, "blocked: no sent quote")
            return quote
        self.api.force_authenticate(self.manager)
        before_total = str(quote.total)
        reprepare = self._prepare(request, markup_pct="90.00")
        resent = None
        if reprepare.status_code in (200, 201):
            resent = self.api.post(
                f"/api/dashboard/partner/quotes/{request.id}/send-to-client/",
                {"broker_margin_type": "percent", "broker_margin_value": "90", "note": "Revised price"},
                format="json",
            )
        latest = request.quotes.order_by("-id").first()
        changed = bool(latest and str(latest.total) != before_total)
        self._record(
            "S4b",
            changed and reprepare.status_code in (200, 201) and resent is not None and resent.status_code == 200,
            f"prepare={reprepare.status_code} send={getattr(resent,'status_code',None)} "
            f"total_before={before_total} total_after={getattr(latest,'total',None)} body={self._dump(reprepare)[:220]}",
        )
        self.api.force_authenticate(self.buyer)
        responses = self.api.get("/api/client/responses/")
        payload = responses.json() if responses.status_code == 200 else []
        rows = payload.get("results", []) if isinstance(payload, dict) else payload
        updated = [row for row in rows if isinstance(row, dict) and row.get("price_updated")]
        self._record(
            "S4b.buyer_flag",
            bool(updated),
            f"status={responses.status_code} price_updated_rows={len(updated)}",
        )
        return latest or quote

    # ------------------------------------------------------------- S5 / S6

    def _s5_buyer_decision(self, request, quote):
        if not quote:
            self._record("S5", None, "blocked: no quote")
            return
        # Reject path on the other request (Job B).
        reject_request = (
            QuoteRequest.objects.filter(assigned_manager=self.manager)
            .exclude(pk=request.pk).order_by("-id").first()
        )
        self.api.force_authenticate(self.manager)
        reject_quote = None
        if reject_request:
            prepared = self._prepare(reject_request)
            if prepared.status_code == 201:
                self.api.post(
                    f"/api/dashboard/partner/quotes/{reject_request.id}/send-to-client/",
                    {"broker_margin_type": "percent", "broker_margin_value": "75"},
                    format="json",
                )
                reject_quote = reject_request.quotes.order_by("-id").first()
        self.api.force_authenticate(self.buyer)
        reject = (
            self.api.post(
                f"/api/client/responses/{reject_quote.id}/reject/",
                {"reason": "sim_reject", "message": "Simulation reject"},
                format="json",
            )
            if reject_quote else None
        )
        self._record(
            "S5.reject",
            reject is not None and reject.status_code in (200, 201),
            f"quote={getattr(reject_quote,'id',None)} status={getattr(reject,'status_code',None)}",
        )
        # A revised quote carries a request-level price_updated flag; the buyer
        # acknowledges the NEW price before payment is allowed.
        request.refresh_from_db()
        if request.price_updated:
            ack = self.api.post(f"/api/client/responses/{quote.id}/acknowledge/", {}, format="json")
            self._record("S5.acknowledge", ack.status_code in (200, 201), f"status={ack.status_code}")
            request.refresh_from_db()
        accepted = self.api.post(
            f"/api/quotes/{quote.id}/accept/",
            {"phone_number": "0700000001"},
            format="json",
        )
        self._record(
            "S5.accept",
            accepted.status_code == 200,
            f"quote={quote.id} status={accepted.status_code} body={self._dump(accepted)[:200]}",
        )

    def _s6_payment(self, quote):
        if not quote:
            self._record("S6", None, "blocked: no quote")
            return None, None
        self.api.force_authenticate(self.buyer)
        payment = Payment.objects.filter(quote=quote).order_by("-id").first()
        if not payment:
            accepted = self.api.post(f"/api/quotes/{quote.id}/accept/", {"phone_number": "0700000001"}, format="json")
            payment = Payment.objects.filter(quote=quote).order_by("-id").first()
        if not payment:
            self._record("S6", False, "no payment record created")
            return None, None
        stk = self.api.post(
            "/api/payments/stk-push/",
            {"payment_id": payment.id, "phone_number": "0700000001"},
            format="json",
        )
        stk_request = payment.mpesa_stk_requests.order_by("-id").first()
        self._record("S6.stk", stk.status_code == 201 and stk_request is not None, f"status={stk.status_code} stk_request={getattr(stk_request,'id',None)}")
        if stk_request is None:
            return None, payment
        callback = self.api.post(
            "/api/payments/mpesa-callback/",
            self._success_callback(stk_request, str(payment.amount)),
            format="json",
        )
        payment.refresh_from_db()
        managed_job = ManagedJob.objects.filter(source_quote=quote).order_by("-id").first()
        self._record(
            "S6.callback",
            callback.status_code == 200 and payment.status == Payment.STATUS_PAID and managed_job is not None,
            f"callback={callback.status_code} payment={payment.status} job={getattr(managed_job,'id',None)}",
        )
        before = payment.status
        duplicate = self.api.post(
            "/api/payments/mpesa-callback/",
            self._success_callback(stk_request, str(payment.amount), receipt="SIMRCPT2"),
            format="json",
        )
        payment.refresh_from_db()
        self._record("S6.idempotent", duplicate.status_code == 200 and payment.status == before, f"status={duplicate.status_code} payment={payment.status}")
        return managed_job, payment

    # ------------------------------------------------------------- S7-S12

    def _s7_artwork(self, managed_job):
        self.api.force_authenticate(self.buyer)
        upload = self.api.post(
            f"/api/managed-jobs/{managed_job.id}/files/artwork/",
            {"file": SimpleUploadedFile("job-artwork.pdf", b"%PDF-1.4 job artwork")},
            format="multipart",
        )
        self._record("S7.upload", upload.status_code == 201, f"status={upload.status_code} body={self._dump(upload)[:160]}")
        self.api.force_authenticate(self.manager)
        request_confirm = self.api.post(
            f"/api/managed-jobs/{managed_job.id}/artwork-confirmation/request/",
            {"note": "Please approve the artwork"}, format="json",
        )
        self._record("S7.request_approval", request_confirm.status_code == 200, f"status={request_confirm.status_code}")
        self.api.force_authenticate(self.buyer)
        respond = self.api.post(
            f"/api/managed-jobs/{managed_job.id}/artwork-confirmation/respond/",
            {"action": "approve"}, format="json",
        )
        self._record("S7.approve", respond.status_code == 200, f"status={respond.status_code} body={self._dump(respond)[:160]}")

    def _s8_artwork_check(self, managed_job):
        self.api.force_authenticate(self.printer)
        files = self.api.get(f"/api/managed-jobs/{managed_job.id}/files/")
        rows = files.json() if files.status_code == 200 else []
        self._record("S8.printer_file", files.status_code == 200 and any(f.get("file_type") == "artwork" for f in rows), f"status={files.status_code} files={len(rows)}")

    def _s9_production(self, managed_job):
        self.api.force_authenticate(self.manager)
        dispatch = self.api.post(f"/api/dashboard/partner/jobs/{managed_job.id}/dispatch/", {"shop_id": self.shop.id}, format="json")
        self._record("S9.dispatch", dispatch.status_code == 201, f"status={dispatch.status_code} body={self._dump(dispatch)[:200]}")
        self.api.force_authenticate(self.printer)
        assignments = self.api.get("/api/shop/assignments/")
        rows = assignments.json() if assignments.status_code == 200 else []
        assignment_id = rows[0].get("id") if rows and isinstance(rows, list) else None
        if not assignment_id:
            self._record("S9.stages", False, f"no assignment status={assignments.status_code}")
            return
        results = []
        for path in ("accept", "mark-in-production", "mark-finishing", "mark-ready"):
            response = self.api.post(f"/api/job-assignments/{assignment_id}/{path}/", {"note": path}, format="json")
            results.append((path, response.status_code))
        self._record("S9.stages", all(code in (200, 201) for _, code in results), f"{results}")

    def _s10_dispatch_delivery(self, managed_job):
        self.api.force_authenticate(self.manager)
        delivered = self.api.post(f"/api/managed-jobs/{managed_job.id}/delivery/mark-delivered/", {"note": "Handed over"}, format="json")
        self._record("S10.delivered", delivered.status_code in (200, 201), f"status={delivered.status_code}")
        self.api.force_authenticate(self.buyer)
        confirm = self.api.post(f"/api/managed-jobs/{managed_job.id}/delivery/confirm/", {}, format="json")
        self._record("S10.confirm", confirm.status_code in (200, 201), f"status={confirm.status_code}")

    def _s11_payout(self, managed_job, payment):
        settlement = self.api.get(f"/api/managed-jobs/{managed_job.id}/settlement/")
        self._record("S11.settlement", settlement.status_code in (200, 403), f"status={settlement.status_code}")
        self.api.force_authenticate(self.admin)
        release = self.api.post(f"/api/managed-jobs/{managed_job.id}/payouts/release/", {}, format="json")
        self._record("S11.release", release.status_code in (200, 201), f"status={release.status_code} body={self._dump(release)[:160]}")

    def _s12_tracking(self, managed_job):
        self.api.force_authenticate(None)
        code = managed_job.managed_reference
        good = self.api.get(f"/api/track/{code}/?phone=0700000001")
        bad = self.api.get(f"/api/track/{code}/?phone=0700009999")
        self._record("S12.phone", good.status_code == 200 and bad.status_code == 404, f"good={good.status_code} bad={bad.status_code}")
        self.api.force_authenticate(self.buyer)
        signed_in = self.api.get(f"/api/track/{code}/")
        self._record("S12.signed_in", signed_in.status_code == 200, f"status={signed_in.status_code}")

    # ------------------------------------------------------------- S13 / S14

    def _s13_manager_added_client(self):
        self.api.force_authenticate(self.manager)
        created = self.api.post(
            "/api/dashboard/partner/clients/",
            {"name": "Phone Client", "phone": "254700000077"}, format="json",
        )
        self._record("S13.client", created.status_code == 201, f"status={created.status_code} offline={created.json().get('is_offline') if created.status_code==201 else None}")
        draft = self.api.post(
            "/api/dashboard/partner/quotes/create/",
            {
                "shop": self.shop.id,
                "title": "Phone-only job",
                "client_name": "Phone Client",
                "client_phone": "254700000077",
                "calculator_inputs_snapshot": {"product_type": "business_card", "quantity": 100},
                "pricing_snapshot": {
                    "selected_shops": [{
                        "id": self.shop.id, "slug": self.shop.slug,
                        "preview": {
                            "totals": {"subtotal": "1000.00", "grand_total": "1000.00"},
                            "breakdown": {
                                "imposition": {"good_sheets": 10},
                                "paper": {"total": "400.00", "quantity": 10},
                                "printing": {"total": "600.00", "quantity": 10},
                            },
                        },
                    }]
                },
                "partner_markup": "250.00",
                "save_as_draft": True,
            },
            format="json",
        )
        quote_request_id = draft.json().get("quote_request_id") if draft.status_code == 201 else None
        sent = None
        if quote_request_id:
            sent = self.api.post(
                f"/api/dashboard/partner/quotes/{quote_request_id}/send-to-client/",
                {"broker_margin_type": "fixed", "broker_margin_value": "250.00", "phone_number": "254700000077"},
                format="json",
            )
        fee_waived = None
        printy_fee = None
        if quote_request_id:
            from quotes.models import Quote
            quote = Quote.objects.filter(quote_request_id=quote_request_id).order_by("-id").first()
            split = getattr(quote, "financial_split", None) if quote else None
            fee_waived = bool(getattr(split, "fee_waived", None)) if split else None
            printy_fee = getattr(split, "printy_fee", None) if split else None
        self._record(
            "S13.waiver",
            sent is not None and sent.status_code == 200 and fee_waived is True
            and (printy_fee is None or Decimal(printy_fee) == Decimal("0.00")),
            f"draft={draft.status_code} send={getattr(sent,'status_code',None)} "
            f"fee_waived={fee_waived} printy_fee={printy_fee}",
        )

    def _s14_role_matrix(self):
        checks = []
        for user, path in (
            (self.manager, "/api/calculator/drafts/"),
            (self.printer, "/api/calculator/drafts/"),
        ):
            self.api.force_authenticate(user)
            checks.append((path, self.api.post(path, {"calculator_inputs_snapshot": self.spec}, format="json").status_code))
        self.api.force_authenticate(self.buyer)
        manager_endpoint = self.api.get("/api/dashboard/partner/quotes/")
        checks.append(("buyer->partner", manager_endpoint.status_code))
        self.api.force_authenticate(self.manager)
        admin_endpoint = self.api.get("/api/dashboard/admin/")
        checks.append(("manager->admin", admin_endpoint.status_code))
        ok = (
            all(code == 403 for path, code in checks if "calculator" in path)
            and manager_endpoint.status_code == 403
            and admin_endpoint.status_code in (403, 404)
        )
        self._record("S14", ok, f"{checks}")
