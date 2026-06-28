"""ManagedJob creation services for additive quote-to-job orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.services.roles import is_broker, is_client
from api.visibility import (
    TOPOLOGY_MANAGED,
    resolve_topology_mode_for_quote_request,
)
from jobs.audit_services import (
    EVENT_ASSIGNMENT_CREATED,
    EVENT_MANAGED_JOB_CREATED,
    EVENT_QUOTE_ACCEPTED,
    record_job_status_event,
)
from jobs.file_services import import_legacy_files_to_managed_job, notify_missing_artwork, sync_managed_job_artwork_requirement
from jobs.choices import ManagedJobTopologyType
from jobs.models import JobAssignment, ManagedJob
from jobs.workflow import assignment_status_from_production_order, managed_status_from_quote_status
from production.models import ProductionOrder
from quotes.models import CalculatorDraft, QuoteRequest, Quote
from services.pricing.urgency import determine_operational_priority, normalize_urgency_type


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _resolve_source_draft(quote_request: QuoteRequest | None) -> CalculatorDraft | None:
    if not quote_request:
        return None
    return getattr(quote_request, "source_draft", None)


def _resolve_client(quote_request: QuoteRequest | None):
    if not quote_request:
        return None
    on_behalf_of = getattr(quote_request, "on_behalf_of", None)
    if on_behalf_of is not None:
        return on_behalf_of

    created_by = getattr(quote_request, "created_by", None)
    if created_by and is_broker(created_by):
        raise ValueError(
            "Partner quote is missing client attribution. Set on_behalf_of before creating a managed job."
        )
    if created_by and is_client(created_by):
        return created_by
    return None


def _resolve_customer(quote_request: QuoteRequest | None):
    return None


def _resolve_relationship_snapshot(customer, quote_request: QuoteRequest | None = None) -> dict[str, Any]:
    if not customer:
        assigned_manager = getattr(quote_request, "assigned_manager", None) if quote_request else None
        if assigned_manager is not None:
            return {
                "owner_type": "user",
                "owner_reference": f"user:{assigned_manager.id}",
                "owner_user_id": assigned_manager.id,
                "owner_shop_id": None,
                "acquisition_source": "partner",
            }
        return {}
    return {
        "owner_type": customer.relationship_owner_type,
        "owner_reference": customer.relationship_owner_reference(),
        "owner_user_id": customer.relationship_owner_user_id,
        "owner_shop_id": customer.relationship_owner_shop_id,
        "acquisition_source": customer.acquisition_source,
    }


def _resolve_broker(customer, quote_request: QuoteRequest | None = None):
    assigned_manager = getattr(quote_request, "assigned_manager", None) if quote_request else None
    if assigned_manager is not None:
        return assigned_manager
    if not customer:
        return None
    if getattr(customer, "relationship_owner_type", "") == "user":
        return customer.relationship_owner_user
    return None


def _resolve_fulfillment_mode(quote_request: QuoteRequest | None) -> str:
    request_snapshot = _as_dict(getattr(quote_request, "request_snapshot", None))
    request_details = _as_dict(request_snapshot.get("request_details"))
    delivery_preference = (
        request_details.get("delivery_preference")
        or getattr(quote_request, "delivery_preference", "")
        or ""
    ).strip().lower()
    if delivery_preference == "delivery":
        return "printy_rider"
    return "pickup"


def _resolve_topology_type(customer, quote_request: QuoteRequest | None = None) -> str:
    if getattr(quote_request, "assigned_manager_id", None):
        return ManagedJobTopologyType.CLIENT_PARTNER
    if customer and getattr(customer, "relationship_owner_type", "") == "user":
        return ManagedJobTopologyType.CLIENT_PARTNER
    return ManagedJobTopologyType.CLIENT_PRINTY_SUPPORT


def _resolve_urgency_payload(*, quote_request: QuoteRequest | None, quote: Quote | None) -> dict[str, Any]:
    response_snapshot = _as_dict(getattr(quote, "response_snapshot", None))
    revised_snapshot = _as_dict(getattr(quote, "revised_pricing_snapshot", None))
    request_snapshot = _as_dict(getattr(quote_request, "request_snapshot", None))
    request_details = _as_dict(request_snapshot.get("request_details"))

    turnaround_hours = getattr(quote, "turnaround_hours", None)
    turnaround_label = getattr(quote, "turnaround_label", "") or response_snapshot.get("turnaround_label")
    urgency_type = normalize_urgency_type(
        response_snapshot.get("urgency_type") or revised_snapshot.get("urgency_type") or request_details.get("urgency_type"),
        turnaround_hours=turnaround_hours,
        turnaround_label=turnaround_label,
    )
    priority_level = determine_operational_priority(
        urgency_type=urgency_type,
        turnaround_hours=turnaround_hours,
        turnaround_label=turnaround_label,
    )

    def _coerce_datetime(value: Any):
        if not value:
            return None
        if hasattr(value, "tzinfo"):
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
        return None

    return {
        "urgency_type": urgency_type,
        "urgency_multiplier": response_snapshot.get("urgency_multiplier") or revised_snapshot.get("urgency_multiplier"),
        "urgency_fee": response_snapshot.get("urgency_fee") or revised_snapshot.get("urgency_fee"),
        "after_hours_fee": response_snapshot.get("after_hours_fee") or revised_snapshot.get("after_hours_fee"),
        "requested_deadline": _coerce_datetime(request_details.get("requested_deadline")),
        "requested_delivery_time": _coerce_datetime(request_details.get("requested_delivery_time")),
        "operational_priority_level": priority_level,
    }


def _build_quote_money_snapshot(*, quote_request: QuoteRequest, quote: Quote, source_draft: CalculatorDraft | None) -> dict[str, Any]:
    request_snapshot = _as_dict(getattr(quote_request, "request_snapshot", None))
    customer_pricing = _as_dict(request_snapshot.get("customer_pricing"))
    financials = getattr(quote, "financial_split", None)
    client_total = (
        financials.client_total
        if financials is not None
        else customer_pricing.get("final_client_price")
    )
    production_cost = (
        financials.production_cost
        if financials is not None
        else customer_pricing.get("production_cost")
    )
    return {
        "quote_request_id": quote_request.id,
        "quote_request_reference": quote_request.request_reference,
        "quote_id": quote.id,
        "quote_reference": quote.quote_reference,
        "quote_status": quote_request.status,
        "quote_status": quote.status,
        "currency": getattr(quote.shop, "currency", "KES") or "KES",
        "client_total": str(client_total) if client_total is not None else None,
        "response_snapshot": _as_dict(quote.response_snapshot),
        "revised_pricing_snapshot": _as_dict(quote.revised_pricing_snapshot),
        "request_customer_pricing": _as_dict(request_snapshot.get("customer_pricing")),
        "production_cost": str(production_cost) if production_cost is not None else None,
        "gross_margin": str(financials.gross_margin) if financials is not None else customer_pricing.get("gross_margin"),
        "printy_fee": str(financials.printy_fee) if financials is not None else customer_pricing.get("printy_fee"),
        "shop_payout": str(financials.shop_payout) if financials is not None else customer_pricing.get("shop_payout"),
        "broker_payout": str(financials.broker_payout) if financials is not None else customer_pricing.get("broker_payout"),
        "final_client_price": str(client_total) if client_total is not None else customer_pricing.get("final_client_price"),
        "source_draft_reference": getattr(source_draft, "draft_reference", ""),
        "visibility": {
            "topology_mode": TOPOLOGY_MANAGED,
            "exposes_internal_economics": False,
        },
    }


def _build_operational_snapshot(*, quote_request: QuoteRequest, quote: Quote, source_draft: CalculatorDraft | None) -> dict[str, Any]:
    request_snapshot = _as_dict(getattr(quote_request, "request_snapshot", None))
    urgency_payload = _resolve_urgency_payload(quote_request=quote_request, quote=quote)
    return {
        "quote_request_id": quote_request.id,
        "shop_id": quote.shop_id,
        "shop_slug": getattr(quote.shop, "slug", ""),
        "selected_shop": _as_dict(request_snapshot.get("selected_shop")),
        "selected_shop_preview": _as_dict(request_snapshot.get("selected_shop_preview")),
        "matched_specs": request_snapshot.get("matched_specs") or [],
        "needs_confirmation": request_snapshot.get("needs_confirmation") or [],
        "delivery_preference": getattr(quote_request, "delivery_preference", ""),
        "delivery_address": getattr(quote_request, "delivery_address", ""),
        "delivery_location_id": getattr(quote_request, "delivery_location_id", None),
        "urgency_type": urgency_payload["urgency_type"],
        "urgency_multiplier": urgency_payload["urgency_multiplier"],
        "urgency_fee": urgency_payload["urgency_fee"],
        "after_hours_fee": urgency_payload["after_hours_fee"],
        "requested_deadline": urgency_payload["requested_deadline"].isoformat() if urgency_payload["requested_deadline"] else None,
        "requested_delivery_time": urgency_payload["requested_delivery_time"].isoformat() if urgency_payload["requested_delivery_time"] else None,
        "operational_priority_level": urgency_payload["operational_priority_level"],
        "source_draft_reference": getattr(source_draft, "draft_reference", ""),
        "topology_mode": resolve_topology_mode_for_quote_request(quote_request),
    }


def _build_assignment_snapshot(*, managed_job: ManagedJob, quote: Quote) -> dict[str, Any]:
    return {
        "managed_job_id": managed_job.id,
        "managed_reference": managed_job.managed_reference,
        "source_quote_id": quote.id,
        "shop_id": quote.shop_id,
        "shop_slug": getattr(quote.shop, "slug", ""),
        "topology_type": managed_job.topology_type,
        "fulfillment_mode": managed_job.fulfillment_mode,
        "urgency_type": managed_job.urgency_type,
        "operational_priority_level": managed_job.operational_priority_level,
        "requested_deadline": managed_job.requested_deadline.isoformat() if managed_job.requested_deadline else None,
    }


@transaction.atomic
def create_managed_job_from_accepted_quote(
    *,
    quote_request: QuoteRequest,
    quote: Quote,
    accepted_by=None,
) -> ManagedJob:
    managed_job = (
        ManagedJob.objects.select_related("source_production_order")
        .filter(source_quote=quote)
        .first()
    )
    if managed_job:
        import_legacy_files_to_managed_job(
            managed_job=managed_job,
            quote_request=quote_request,
            quote=quote,
        )
        return managed_job

    source_draft = _resolve_source_draft(quote_request)
    customer = _resolve_customer(quote_request)
    broker = _resolve_broker(customer, quote_request=quote_request)
    topology_mode = resolve_topology_mode_for_quote_request(quote_request)
    urgency_payload = _resolve_urgency_payload(quote_request=quote_request, quote=quote)
    request_snapshot = _as_dict(getattr(quote_request, "request_snapshot", None))
    customer_pricing = _as_dict(request_snapshot.get("customer_pricing"))
    financials = getattr(quote, "financial_split", None)
    client_total = financials.client_total if financials is not None else customer_pricing.get("final_client_price") or quote.total
    initial_assigned_shop = quote.shop if broker is None else None
    initial_assignment_status = "assignment_pending" if initial_assigned_shop else "unassigned"

    managed_job = ManagedJob.objects.create(
        title=quote.note[:255] if quote.note else (quote_request.notes[:255] if quote_request.notes else f"Managed job from quote {quote.quote_reference or quote.id}"),
        source_quote_request=quote_request,
        source_quote=quote,
        client=_resolve_client(quote_request),
        broker=broker,
        assigned_shop=initial_assigned_shop,
        created_by=accepted_by or _resolve_client(quote_request) or quote.created_by,
        status=managed_status_from_quote_status(quote.status),
        payment_status="pending",
        assignment_status=initial_assignment_status,
        exception_status="clear",
        fulfillment_mode=_resolve_fulfillment_mode(quote_request),
        topology_type=_resolve_topology_type(customer, quote_request=quote_request),
        urgency_type=urgency_payload["urgency_type"],
        urgency_multiplier=urgency_payload["urgency_multiplier"],
        urgency_fee=urgency_payload["urgency_fee"],
        after_hours_fee=urgency_payload["after_hours_fee"],
        requested_deadline=urgency_payload["requested_deadline"],
        requested_delivery_time=urgency_payload["requested_delivery_time"],
        operational_priority_level=urgency_payload["operational_priority_level"],
        client_total=client_total,
        operational_snapshot=_build_operational_snapshot(
            quote_request=quote_request,
            quote=quote,
            source_draft=source_draft,
        ),
        workflow_metadata={
            "created_from": "accepted_quote",
            "accepted_via_quote_request_id": quote_request.id,
            "accepted_via_quote_id": quote.id,
            "topology_mode": topology_mode,
        },
        relationship_snapshot=_resolve_relationship_snapshot(customer, quote_request=quote_request),
        accepted_at=quote.accepted_at or timezone.now(),
    )
    import_legacy_files_to_managed_job(
        managed_job=managed_job,
        quote_request=quote_request,
        quote=quote,
    )
    has_artwork = sync_managed_job_artwork_requirement(managed_job=managed_job)
    record_job_status_event(
        managed_job=managed_job,
        actor=accepted_by or managed_job.created_by,
        event_type=EVENT_QUOTE_ACCEPTED,
        summary="Accepted quote linked to managed job.",
        metadata={
            "quote_request_id": quote_request.id,
            "quote_id": quote.id,
        },
    )
    record_job_status_event(
        managed_job=managed_job,
        actor=accepted_by or managed_job.created_by,
        event_type=EVENT_MANAGED_JOB_CREATED,
        summary="Managed job created from accepted quote.",
        metadata={
            "quote_request_id": quote_request.id,
            "quote_id": quote.id,
            "topology_mode": topology_mode,
        },
    )
    if initial_assigned_shop is not None:
        create_assignment_for_managed_job(
            managed_job=managed_job,
            quote=quote,
        )
    if not has_artwork:
        notify_missing_artwork(
            managed_job=managed_job,
            actor=accepted_by or managed_job.created_by,
            source="quote_accepted",
        )
    return managed_job


@transaction.atomic
def create_assignment_for_managed_job(
    *,
    managed_job: ManagedJob,
    quote: Quote | None = None,
) -> JobAssignment:
    assignment = (
        JobAssignment.objects.select_related("production_order")
        .filter(managed_job=managed_job, reassigned_from__isnull=True)
        .first()
    )
    if assignment:
        import_legacy_files_to_managed_job(
            managed_job=managed_job,
            quote_request=managed_job.source_quote_request,
            quote=quote or managed_job.source_quote,
        )
        return assignment

    source_quote = quote or managed_job.source_quote
    assigned_shop = managed_job.assigned_shop or getattr(source_quote, "shop", None)

    assignment = JobAssignment.objects.create(
        managed_job=managed_job,
        assigned_shop=assigned_shop,
        source_quote=source_quote,
        status="pending",
        shop_payout=getattr(getattr(source_quote, "financial_split", None), "shop_payout", None),
        urgency_type=managed_job.urgency_type,
        operational_priority_level=managed_job.operational_priority_level,
        assignment_notes="Initial assignment created from accepted quote.",
        requested_deadline=managed_job.requested_deadline,
        operational_snapshot=_build_assignment_snapshot(
            managed_job=managed_job,
            quote=source_quote,
        ) if source_quote else {
            "managed_job_id": managed_job.id,
            "managed_reference": managed_job.managed_reference,
        },
    )
    import_legacy_files_to_managed_job(
        managed_job=managed_job,
        quote_request=managed_job.source_quote_request,
        quote=source_quote,
    )
    record_job_status_event(
        managed_job=managed_job,
        assignment=assignment,
        actor=managed_job.created_by,
        event_type=EVENT_ASSIGNMENT_CREATED,
        summary="Assignment created for managed job.",
        metadata={
            "assigned_shop_id": assigned_shop.id if assigned_shop else None,
            "source_quote_id": source_quote.id if source_quote else None,
        },
    )
    return assignment


@transaction.atomic
def attach_production_order_to_managed_job(*, managed_job: ManagedJob, production_order: ProductionOrder) -> ManagedJob:
    if managed_job.source_production_order_id != production_order.id:
        managed_job.source_production_order = production_order
        managed_job.operational_snapshot = {
            **_as_dict(managed_job.operational_snapshot),
            "production_order_id": production_order.id,
            "production_order_status": production_order.status,
            "production_delivery_status": production_order.delivery_status,
        }
        managed_job.save(update_fields=["source_production_order", "operational_snapshot", "updated_at"])
    return managed_job


@transaction.atomic
def attach_production_order_to_assignment(*, assignment: JobAssignment, production_order: ProductionOrder) -> JobAssignment:
    next_status = assignment_status_from_production_order(
        status=production_order.status,
        delivery_status=production_order.delivery_status,
    )
    update_fields: list[str] = ["updated_at"]

    if assignment.production_order_id != production_order.id:
        assignment.production_order = production_order
        update_fields.append("production_order")

    if assignment.status != next_status:
        assignment.status = next_status
        update_fields.append("status")

    assignment.operational_snapshot = {
        **_as_dict(assignment.operational_snapshot),
        "production_order_id": production_order.id,
        "production_order_status": production_order.status,
        "production_delivery_status": production_order.delivery_status,
    }
    update_fields.append("operational_snapshot")
    assignment.save(update_fields=update_fields)
    return assignment
