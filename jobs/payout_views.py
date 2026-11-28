"""Automatic payout endpoints -- Prompt 7 PART 2.

- B2C ResultURL / QueueTimeOutURL callbacks (authenticated like STK).
- Super-admin payout API: list, retry failed, approve needs_review, per-job
  money summary (read-only).
- ``GET /api/payouts/mine/``: a shop owner sees its own payouts, a manager
  theirs; clients (no ``can_receive_payouts``) get 403.

No endpoint here lets a client or manager trigger, create or alter a payout.
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.admin_audit import record_admin_audit
from api.permissions import IsSuperUser
from jobs import auto_payouts
from jobs.models import ManagedJob, ManagedJobPayout
from payments.callback_security import reject_unverified_callback

logger = logging.getLogger("payments")

ADMIN_PERMISSIONS = [IsAuthenticated, IsSuperUser]
ACCEPTED = {"ResultCode": 0, "ResultDesc": "Accepted"}


class _B2CCallbackBase(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    kind = "result"

    def post(self, request, callback_token: str | None = None):
        rejection = reject_unverified_callback(request, callback_token, route=f"mpesa-b2c-{self.kind}")
        if rejection is not None:
            return rejection
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            outcome = auto_payouts.process_b2c_callback(payload, kind=self.kind)
        except Exception:  # noqa: BLE001 - never 500 to Daraja; the timeout sweep is the backstop
            logger.exception("B2C %s callback processing failed", self.kind)
            outcome = "error"
        logger.info("B2C %s callback outcome=%s", self.kind, outcome)
        return Response(ACCEPTED)


class B2CResultCallbackView(_B2CCallbackBase):
    """POST /api/payments/mpesa/b2c/result/<secret>/ -- Daraja B2C ResultURL."""

    kind = "result"


class B2CTimeoutCallbackView(_B2CCallbackBase):
    """POST /api/payments/mpesa/b2c/timeout/<secret>/ -- Daraja B2C QueueTimeOutURL."""

    kind = "timeout"


class AdminPayoutListView(APIView):
    """GET /api/admin/payouts/?status=&role=&job=&waiting_reason=&mode="""

    permission_classes = ADMIN_PERMISSIONS

    def get(self, request):
        qs = ManagedJobPayout.objects.select_related("managed_job").order_by("-updated_at", "-id")
        params = request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("role"):
            qs = qs.filter(recipient_role=params["role"])
        if params.get("job"):
            qs = qs.filter(managed_job_id=params["job"])
        if params.get("waiting_reason"):
            qs = qs.filter(waiting_reason=params["waiting_reason"])
        if params.get("mode"):
            qs = qs.filter(disbursement_mode=params["mode"])
        rows = [auto_payouts.payout_row(payout) for payout in qs[:500]]
        return Response(
            {
                "count": len(rows),
                "results": rows,
                "auto_payouts_enabled": auto_payouts.auto_payouts_active(),
            }
        )


def _admin_error(exc: auto_payouts.PayoutAdminError) -> Response:
    return Response({"detail": "; ".join(exc.messages), "code": exc.code_name}, status=status.HTTP_400_BAD_REQUEST)


class AdminPayoutRetryView(APIView):
    """POST /api/admin/payouts/<id>/retry/  {"reason": "..."}"""

    permission_classes = ADMIN_PERMISSIONS

    def post(self, request, payout_id):
        payout = get_object_or_404(ManagedJobPayout, pk=payout_id)
        reason = str(request.data.get("reason") or "").strip()
        try:
            payout = auto_payouts.retry_payout(payout, actor=request.user, reason=reason)
        except auto_payouts.PayoutAdminError as exc:
            return _admin_error(exc)
        record_admin_audit(user=request.user, obj=payout, action="payout_retry", reason=reason,
                           metadata={"payout_id": payout.id})
        return Response(auto_payouts.payout_row(payout))


class AdminPayoutApproveView(APIView):
    """POST /api/admin/payouts/<id>/approve/

    Body: {"confirm_amount": "800.00", "reason": "...", "confirm_not_paid": false}
    """

    permission_classes = ADMIN_PERMISSIONS

    def post(self, request, payout_id):
        payout = get_object_or_404(ManagedJobPayout, pk=payout_id)
        reason = str(request.data.get("reason") or "").strip()
        reviewed_reason = payout.waiting_reason
        try:
            payout = auto_payouts.approve_payout(
                payout,
                actor=request.user,
                confirm_amount=request.data.get("confirm_amount"),
                reason=reason,
                confirm_not_paid=str(request.data.get("confirm_not_paid") or "").lower() in {"1", "true", "yes"},
            )
        except auto_payouts.PayoutAdminError as exc:
            return _admin_error(exc)
        record_admin_audit(
            user=request.user,
            obj=payout,
            action="payout_approve_review",
            reason=reason,
            metadata={"payout_id": payout.id, "approved_reason": reviewed_reason, "amount": str(payout.amount)},
        )
        return Response(auto_payouts.payout_row(payout))


class AdminJobMoneySummaryView(APIView):
    """GET /api/admin/jobs/<job_id>/money-summary/ (read-only)."""

    permission_classes = ADMIN_PERMISSIONS

    def get(self, request, job_id):
        job = get_object_or_404(ManagedJob, pk=job_id)
        return Response(auto_payouts.job_money_summary(job))


class MyPayoutsView(APIView):
    """GET /api/payouts/mine/ -- the caller's own payouts, read-only."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not auto_payouts.can_receive_payouts(request.user):
            return Response({"detail": "Payouts are not available for this account."}, status=status.HTTP_403_FORBIDDEN)
        qs = (
            ManagedJobPayout.objects.select_related("managed_job")
            .filter(recipient=request.user)
            .order_by("-created_at", "-id")
        )
        rows = [auto_payouts.payout_row(payout, admin=False) for payout in qs[:200]]
        return Response({"count": len(rows), "results": rows})
