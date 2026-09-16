"""ViewSets for the workflow module.

The API mirrors printy_workflow's role-agnostic job graph: managers and
printers are published for the admin/manager views, and every transition the
frontend triggers (approve, pay, assign, press-advance, ...) is exposed as a
detail action that runs the same state machine ported in workflow.transitions.

Demo API: endpoints are AllowAny so the workflow demo works without auth.
"""
from decimal import Decimal

from django.db import transaction
from django.urls import NoReverseMatch, reverse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.response import Response

from .models import WorkflowJob, WorkflowManager, WorkflowPrinter
from .serializers import WorkflowJobSerializer, WorkflowManagerSerializer, WorkflowPrinterSerializer
from . import transitions
from .seed import MANAGERS, PRINTERS


def _manager_records():
    return {
        m.id: {"id": m.id, "name": m.name, "initials": m.initials, "tag": m.tag, "onTime": m.on_time, "hue": m.hue}
        for m in WorkflowManager.objects.all()
    }


def _printer_records():
    records = {}
    for p in WorkflowPrinter.objects.all():
        records[p.id] = {
            "id": p.id,
            "name": p.name,
            "contact": p.contact,
            "city": p.city,
            "caps": p.caps,
            "verified": p.verified,
            "rating": p.rating,
            "jobsDone": p.jobs_done,
            "onTime": p.on_time,
            "initials": p.initials,
            "hue": p.hue,
        }
    return records


def _job_state(job):
    """Model -> TS Job dict (same shape the serializer emits)."""
    return {
        "id": job.id,
        "code": job.code,
        "title": job.title,
        "product": job.product,
        "qty": job.qty,
        "value": float(job.value),
        "buyerId": job.buyer_id,
        "buyerName": job.buyer_name,
        "buyerCompany": job.buyer_company,
        "managerId": job.manager_id,
        "printerId": job.printer_id,
        "specs": job.specs,
        "proofImg": job.proof_img or None,
        "status": job.status,
        "custody": job.custody,
        "stage": job.stage,
        "press": job.press,
        "progress": job.progress,
        "owner": job.owner,
        "eta": job.eta,
        "placedAt": job.placed_at,
        "dispute": job.dispute,
        "history": job.history,
        "feed": job.feed,
    }


def _apply_state(job, state):
    """TS Job dict -> model fields."""
    job.code = state["code"]
    job.title = state["title"]
    job.product = state["product"]
    job.qty = state["qty"]
    job.value = Decimal(str(state["value"]))
    job.buyer_id = state["buyerId"]
    job.buyer_name = state["buyerName"]
    job.buyer_company = state["buyerCompany"]
    job.manager = WorkflowManager.objects.get(id=state["managerId"])
    job.printer = WorkflowPrinter.objects.get(id=state["printerId"]) if state["printerId"] else None
    job.specs = state["specs"]
    job.proof_img = state["proofImg"] or ""
    job.status = state["status"]
    job.custody = state["custody"]
    job.stage = state["stage"]
    job.press = state["press"]
    job.progress = state["progress"]
    job.owner = state["owner"]
    job.eta = state["eta"]
    job.placed_at = state["placedAt"]
    job.dispute = state["dispute"]
    job.history = state["history"]
    job.feed = state["feed"]
    job.save()
    return job


class WorkflowBrowsableAPIRenderer(BrowsableAPIRenderer):
    """Browsable API renderer that injects workflow transition buttons."""

    template = "rest_framework/api.html"

    def get_context(self, data, accepted_media_type, renderer_context):
        context = super().get_context(data, accepted_media_type, renderer_context)
        view = renderer_context.get("view")
        provider = getattr(view, "get_workflow_buttons", None)
        context["workflow_action_buttons"] = provider() if provider else []
        return context


class WorkflowManagerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WorkflowManager.objects.all()
    serializer_class = WorkflowManagerSerializer
    permission_classes = [AllowAny]
    pagination_class = None


class WorkflowPrinterViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WorkflowPrinter.objects.all()
    serializer_class = WorkflowPrinterSerializer
    permission_classes = [AllowAny]
    pagination_class = None


class WorkflowJobViewSet(viewsets.ModelViewSet):
    queryset = WorkflowJob.objects.all()
    serializer_class = WorkflowJobSerializer
    permission_classes = [AllowAny]
    pagination_class = None
    renderer_classes = [JSONRenderer, WorkflowBrowsableAPIRenderer]

    def _run_transition(self, request, fn):
        job = self.get_object()
        state = _job_state(job)
        updated = fn(state, request)
        if updated is state:
            return Response(
                {"detail": "No transition applied - job is not in the required stage."},
                status=status.HTTP_409_CONFLICT,
            )
        _apply_state(job, updated)
        return Response(WorkflowJobSerializer(job).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._run_transition(request, lambda s, r: transitions.approve_artwork(s))

    @action(detail=True, methods=["post"], url_path="request-changes")
    def request_changes(self, request, pk=None):
        return self._run_transition(request, lambda s, r: transitions.request_changes(s))

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        mgrs = _manager_records()
        return self._run_transition(request, lambda s, r: transitions.pay_job(s, mgrs))

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        printer_id = request.data.get("printer_id") or request.data.get("printerId")
        if not printer_id:
            return Response(
                {"detail": "printer_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        mgrs = _manager_records()
        prns = _printer_records()
        if printer_id not in prns:
            return Response(
                {"detail": f"Unknown printer: {printer_id}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._run_transition(request, lambda s, r: transitions.assign_printer(s, printer_id, mgrs, prns))

    @action(detail=True, methods=["post"], url_path="press-advance")
    def press_advance(self, request, pk=None):
        prns = _printer_records()
        return self._run_transition(request, lambda s, r: transitions.advance_press(s, prns))

    @action(detail=True, methods=["post"], url_path="confirm-delivery")
    def confirm_delivery(self, request, pk=None):
        prns = _printer_records()
        return self._run_transition(request, lambda s, r: transitions.confirm_delivery(s, prns))

    @action(detail=True, methods=["post"], url_path="resolve-dispute")
    def resolve_dispute(self, request, pk=None):
        mgrs = _manager_records()
        return self._run_transition(request, lambda s, r: transitions.resolve_dispute(s, mgrs))

    @action(detail=True, methods=["post"])
    def nudge(self, request, pk=None):
        by = request.data.get("by") or "Printy"
        return self._run_transition(request, lambda s, r: transitions.nudge(s, by))

    def get_workflow_buttons(self):
        """Transition buttons rendered by the browsable API testing panel.

        One button per state-machine transition so the whole flow can be
        clicked through in the DRF UI. Detail pages get the transitions.
        """
        if getattr(self, "request", None) is None:
            return []
        action = getattr(self, "action", None)
        pk = self.kwargs.get("pk")
        try:
            if action != "retrieve" or not pk:
                return []
            printer_options = [
                {"value": p["id"], "label": f'{p["name"]} ({p["city"]})'}
                for p in sorted(_printer_records().values(), key=lambda r: r["name"])
            ]

            def job_url(action_name):
                return reverse(f"workflow-job-{action_name}", kwargs={"pk": pk})

            return [
                {"label": "Approve artwork", "method": "POST", "url": job_url("approve"), "fields": []},
                {"label": "Request changes", "method": "POST", "url": job_url("request-changes"), "fields": []},
                {"label": "Pay (release to production)", "method": "POST", "url": job_url("pay"), "fields": []},
                {
                    "label": "Assign printer",
                    "method": "POST",
                    "url": job_url("assign"),
                    "fields": [{"type": "select", "name": "printer_id", "options": printer_options}],
                },
                {"label": "Advance press", "method": "POST", "url": job_url("press-advance"), "fields": []},
                {"label": "Confirm delivery", "method": "POST", "url": job_url("confirm-delivery"), "fields": []},
                {"label": "Resolve dispute", "method": "POST", "url": job_url("resolve-dispute"), "fields": []},
                {
                    "label": "Nudge client",
                    "method": "POST",
                    "url": job_url("nudge"),
                    "fields": [{"type": "text", "name": "by", "placeholder": "who nudges (default Printy)"}],
                },
            ]
        except NoReverseMatch:
            return []


@transaction.atomic
def seed_workflow():
    """Idempotent (re)seed of the managers and printers reference roster.

    Jobs are intentionally not seeded - they must come from real workflow
    activity (quote -> payment -> assignment) so no phantom jobs appear.
    """
    for record in MANAGERS:
        WorkflowManager.objects.update_or_create(
            id=record["id"],
            defaults={
                "name": record["name"],
                "initials": record["initials"],
                "tag": record["tag"],
                "on_time": record["onTime"],
                "hue": record["hue"],
            },
        )
    for record in PRINTERS:
        WorkflowPrinter.objects.update_or_create(
            id=record["id"],
            defaults={
                "name": record["name"],
                "contact": record["contact"],
                "city": record["city"],
                "caps": record["caps"],
                "verified": record["verified"],
                "rating": record["rating"],
                "jobs_done": record["jobsDone"],
                "on_time": record["onTime"],
                "initials": record["initials"],
                "hue": record["hue"],
            },
        )