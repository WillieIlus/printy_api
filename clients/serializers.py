"""Serializers for the managed-client flow (law 10.4-10.6)."""
from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers


class CreateRequestForClientSerializer(serializers.Serializer):
    """Law 10.5: a manager creates a quote request for a client."""

    name = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=32)
    contact_permitted = serializers.BooleanField()
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    company = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    delivery_preference = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    details = serializers.DictField(required=False, default=dict)

    def validate_contact_permitted(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "Confirm you have the client's permission to contact them."
            )
        return value


class OtpRequestSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")


class OtpVerifySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12)


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def build_link_payload(quote_request, *, otp_verified: bool = False) -> dict:
    """The buyer projection for a client link (law 10.6 / 7.9).

    Only this one request is exposed. No shop names, no printer details and no
    other jobs ever appear.
    """
    from jobs.job_codes import quote_request_code
    from jobs.timing import stage_timeline, timing_fields
    from services.pricing.role_projection import (
        assert_projection_is_clean,
        project_canonical,
        projection_role_for_actor,
    )

    buyer_role = projection_role_for_actor("client")
    code = quote_request_code(quote_request)

    job = quote_request.managed_jobs.order_by("-id").first()

    payload: dict = {
        "code": code,
        "quote_reference": quote_request.request_reference or code,
        "status": quote_request.status,
        "status_label": quote_request.get_status_display(),
        "created_at": _iso(quote_request.created_at),
        "delivery_preference": quote_request.delivery_preference or "",
        "otp_verified": bool(otp_verified),
        "is_job": job is not None,
        "payment_status": None,
        "payment_state": None,
        "payment_state_label": None,
        "due_date": None,
        "timeline": [],
    }

    if job is not None:
        from payments.states import canonical_job_payment_state, payment_state_label

        events = list(job.job_events.all()) if hasattr(job, "job_events") else []
        timeline = stage_timeline(job, events=events)
        timing = project_canonical(timing_fields(job), buyer_role)
        assert_projection_is_clean(timing, buyer_role)
        payload["payment_status"] = job.payment_status
        state = canonical_job_payment_state(job)
        payload["payment_state"] = state
        payload["payment_state_label"] = payment_state_label(state)
        payload["due_date"] = _iso(timing.get("due_date")) if timing.get("due_date") else None
        payload["timeline"] = timeline

    return payload


__all__ = [
    "CreateRequestForClientSerializer",
    "OtpRequestSerializer",
    "OtpVerifySerializer",
    "build_link_payload",
]
