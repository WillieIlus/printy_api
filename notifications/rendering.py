"""Role-scoped, language-aware rendering for structured notifications.

A notification row stores ``template_key`` + ``params`` (and, as a fallback, a
legacy ``message``). This module turns that record into a small, safe payload
that the UI and future channel adapters (email/SMS/WhatsApp) can both consume:

    {
        "title": "Quote ready: 300 business cards",
        "body": [{"text": "Quote #2", "bold": true, "link": None}, ...],
        "action_url": "/app/buyer?tab=quote",
        "action_label": "Review & pay",
        "priority": "action_required",
    }

Rendering happens per recipient-role so we never leak identity or economics:
a buyer never sees the shop's identity, a printer never sees what the client
paid. It also renders per language, so Swahili can be added later without a
data migration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from accounts.services.roles import (
    CANONICAL_CLIENT_ROLE,
    CANONICAL_PARTNER_ROLE,
    CANONICAL_PRODUCTION_ROLE,
    CANONICAL_SUPER_ADMIN_ROLE,
    resolve_user_roles,
)

from .models import Notification

logger = logging.getLogger(__name__)

VIEWER_ADMIN = "admin"
VIEWER_SHOP = "shop"
VIEWER_MANAGER = "manager"
VIEWER_CLIENT = "client"
VIEWER_PUBLIC = "public"

EMPTY_SEGMENT: dict[str, Any] = {"text": "", "bold": False, "link": None}


def segment(text: Any, *, bold: bool = False, link: str | None = None) -> dict[str, Any]:
    """Build one safe body segment (no HTML/markdown is ever accepted)."""
    return {"text": "" if text is None else str(text), "bold": bold, "link": link}


def _decorate(segments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [s for s in (segments or []) if s and s.get("text")]


# ── Localisation ─────────────────────────────────────────────────────────────
# Strings are resolved at read time. Only the phrases we have translated live
# here; anything missing falls back to the English default passed by the
# template, so adding a language never requires a migration.
STRINGS: dict[str, dict[str, str]] = {
    "sw": {
        "quote_ready.title": "Nukuu iko tayari: {summary}",
        "quote_ready.action": "Angalia na ulipe",
        "quote_updated.title": "Nukuu imesasishwa",
        "payment_received.title": "Malipo yamepokelewa",
        "artwork_needed.title": "Mchoro unahitajika",
        "artwork_needed.action": "Pakia mchoro",
        "new_request.title": "Ombi jipya la nukuu",
        "request_cancelled.title": "Ombi limesitishwa",
        "question.title": "Swali kutoka kwa mshirika wako wa uchapishaji",
        "quote_accepted.title": "Nukuu imekubaliwa",
        "view_request.action": "Angalia ombi",
        "review_request.action": "Kagua ombi",
        "reply.action": "Jibu",
    },
}

CURRENCY_CODE = "KES"
DEFAULT_VALIDITY_HOURS = 48


def translate(key: str, lang: str, default: str) -> str:
    lang = (lang or "en").split("-")[0].lower()
    if lang == "en":
        return default
    return STRINGS.get(lang, {}).get(key, default)


# ── Viewer role resolution ───────────────────────────────────────────────────
def _viewer_from_roles(user: Any) -> str:
    roles = set(resolve_user_roles(user))
    if CANONICAL_SUPER_ADMIN_ROLE in roles or getattr(user, "is_staff", False):
        return VIEWER_ADMIN
    if CANONICAL_PRODUCTION_ROLE in roles:
        return VIEWER_SHOP
    if CANONICAL_PARTNER_ROLE in roles:
        return VIEWER_MANAGER
    if CANONICAL_CLIENT_ROLE in roles:
        return VIEWER_CLIENT
    return VIEWER_PUBLIC


def resolve_entity_role(notification: Notification, user: Any) -> str:
    """Resolve how this recipient relates to the notification's entity."""
    if user is None:
        return VIEWER_PUBLIC
    fallback = _viewer_from_roles(user)
    if fallback == VIEWER_ADMIN:
        return VIEWER_ADMIN

    user_id = getattr(user, "id", None)
    entity = load_entity(notification)
    if entity is None:
        return fallback

    object_type = notification.object_type
    try:
        if object_type == "quote_request":
            if getattr(entity, "assigned_manager_id", None) == user_id:
                return VIEWER_MANAGER
            if getattr(getattr(entity, "shop", None), "owner_id", None) == user_id:
                return VIEWER_SHOP
            if getattr(entity, "created_by_id", None) == user_id:
                return VIEWER_CLIENT
            if getattr(entity, "on_behalf_of_id", None) == user_id:
                return VIEWER_CLIENT
        elif object_type == "quote":
            quote_request = getattr(entity, "quote_request", None)
            if getattr(entity, "shop", None) and getattr(entity.shop, "owner_id", None) == user_id:
                return VIEWER_SHOP
            if getattr(quote_request, "assigned_manager_id", None) == user_id:
                return VIEWER_MANAGER
            if getattr(quote_request, "created_by_id", None) == user_id:
                return VIEWER_CLIENT
            if getattr(quote_request, "on_behalf_of_id", None) == user_id:
                return VIEWER_CLIENT
        elif object_type == "managed_job":
            if getattr(entity, "client_id", None) == user_id:
                return VIEWER_CLIENT
            if getattr(entity, "broker_id", None) == user_id:
                return VIEWER_MANAGER
            if getattr(getattr(entity, "assigned_shop", None), "owner_id", None) == user_id:
                return VIEWER_SHOP
        elif object_type in {"production_order", "job_assignment"}:
            if getattr(getattr(entity, "assigned_shop", None), "owner_id", None) == user_id:
                return VIEWER_SHOP
            if getattr(getattr(entity, "shop", None), "owner_id", None) == user_id:
                return VIEWER_SHOP
    except Exception as exc:  # noqa: BLE001 - never let rendering break the API
        logger.warning("Failed to resolve entity role: %s", exc)
    return fallback


def load_entity(notification: Notification) -> Any | None:
    object_type = notification.object_type
    object_id = notification.object_id
    if not object_type or object_id is None:
        return None
    try:
        if object_type == "quote_request":
            from quotes.models import QuoteRequest

            return (
                QuoteRequest.objects.select_related("shop", "created_by", "assigned_manager", "on_behalf_of")
                .filter(pk=object_id)
                .first()
            )
        if object_type == "quote":
            from quotes.models import Quote

            return (
                Quote.objects.select_related(
                    "shop",
                    "quote_request",
                    "quote_request__created_by",
                    "quote_request__assigned_manager",
                    "quote_request__on_behalf_of",
                )
                .filter(pk=object_id)
                .first()
            )
        if object_type == "managed_job":
            from jobs.models import ManagedJob

            return (
                ManagedJob.objects.select_related("client", "broker", "assigned_shop")
                .filter(pk=object_id)
                .first()
            )
        if object_type == "job_assignment":
            from jobs.models import JobAssignment

            return (
                JobAssignment.objects.select_related("assigned_shop", "managed_job")
                .filter(pk=object_id)
                .first()
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load notification entity %s#%s: %s", object_type, object_id, exc)
    return None


# ── Target URL (recipient specific) ──────────────────────────────────────────
def resolve_target_url(notification: Notification) -> str | None:
    """Build the frontend deep link for the notification target.

    Frontend dashboard routes live under /app/* (single-page dashboards), so
    deep links resolve to the role dashboard rather than a legacy /dashboard/*
    URL.
    """
    try:
        if not notification.object_type or notification.object_id is None:
            return None
        ot, oid = notification.object_type, notification.object_id
        recipient = getattr(notification, "user", None)
        roles = set(resolve_user_roles(recipient))

        if ot == "quote_request":
            from quotes.models import QuoteRequest

            qr = (
                QuoteRequest.objects.select_related(
                    "shop", "created_by", "assigned_manager", "on_behalf_of"
                )
                .filter(pk=oid)
                .first()
            )
            if qr is None:
                return None
            if CANONICAL_SUPER_ADMIN_ROLE in roles:
                return "/app/admin"
            if qr.shop and qr.shop.owner_id == notification.user_id:
                return "/app/printer"
            if qr.assigned_manager_id == notification.user_id or CANONICAL_PARTNER_ROLE in roles:
                return "/app/manager"
            if (
                qr.created_by_id == notification.user_id
                or qr.on_behalf_of_id == notification.user_id
                or CANONICAL_CLIENT_ROLE in roles
            ):
                return "/app/buyer?tab=quote"
            if CANONICAL_PRODUCTION_ROLE in roles:
                return "/app/printer"
            return None

        if ot == "quote":
            from quotes.models import Quote

            sq = (
                Quote.objects.select_related(
                    "shop",
                    "quote_request",
                    "quote_request__created_by",
                    "quote_request__assigned_manager",
                    "quote_request__on_behalf_of",
                )
                .filter(pk=oid)
                .first()
            )
            if sq is None:
                return None
            quote_request = sq.quote_request
            if CANONICAL_SUPER_ADMIN_ROLE in roles:
                return "/app/admin"
            if sq.shop and sq.shop.owner_id == notification.user_id:
                return "/app/printer"
            if quote_request and (
                quote_request.assigned_manager_id == notification.user_id or CANONICAL_PARTNER_ROLE in roles
            ):
                return "/app/manager"
            if quote_request and (
                quote_request.created_by_id == notification.user_id
                or quote_request.on_behalf_of_id == notification.user_id
                or CANONICAL_CLIENT_ROLE in roles
            ):
                return "/app/buyer?tab=quote"
            if CANONICAL_PRODUCTION_ROLE in roles:
                return "/app/printer"
            return None

        if ot == "production_order":
            if CANONICAL_SUPER_ADMIN_ROLE in roles:
                return "/app/admin"
            return "/app/printer"

        if ot in {"managed_job", "job_assignment"}:
            if CANONICAL_SUPER_ADMIN_ROLE in roles:
                return "/app/admin"
            if CANONICAL_PRODUCTION_ROLE in roles:
                return "/app/printer"
            if CANONICAL_PARTNER_ROLE in roles:
                return "/app/manager"
            return "/app/buyer"
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to build notification target URL: %s", exc)
        return None


# ── Small formatting helpers ─────────────────────────────────────────────────
def _money(amount: Any, currency: str = CURRENCY_CODE) -> str:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return f"{currency} {amount}"
    quantized = value.quantize(Decimal("1")) if value == value.to_integral_value() else value
    return f"{currency} {quantized:,.2f}".rstrip("0").rstrip(".") if "." in f"{quantized:,.2f}" else f"{currency} {quantized:,.0f}"


def _quote_summary(quote: Any) -> str:
    try:
        item = quote.items.first() or quote.quote_request.items.first()
    except Exception:  # noqa: BLE001
        item = None
    if item is None:
        return ""
    quantity = getattr(item, "quantity", None)
    label = (
        getattr(item, "product_title", "")
        or getattr(item, "product_name", "")
        or getattr(item, "title", "")
        or getattr(item, "description", "")
    )
    parts = []
    if quantity:
        parts.append(str(quantity))
    if label:
        parts.append(str(label))
    return " ".join(parts).strip()


@dataclass
class RenderContext:
    notification: Notification
    user: Any
    lang: str
    viewer: str
    params: dict[str, Any] = field(default_factory=dict)
    entity: Any = None
    target_url: str | None = None

    def t(self, key: str, default: str) -> str:
        return translate(key, self.lang, default)

    def param(self, *keys: str) -> Any:
        for key in keys:
            if key in self.params and self.params[key] not in (None, ""):
                return self.params[key]
        return None


TemplateFn = Callable[["RenderContext"], dict[str, Any] | None]


def _render_quote_ready(ctx: RenderContext) -> dict[str, Any] | None:
    quote = ctx.entity
    summary = ctx.param("summary") or (_quote_summary(quote) if quote is not None else "")
    total = ctx.param("total") or getattr(quote, "total", None)
    currency = ctx.param("currency") or CURRENCY_CODE
    quote_id = ctx.param("quote_id") or getattr(quote, "id", ctx.notification.object_id)
    hours = ctx.param("validity_hours") or DEFAULT_VALIDITY_HOURS

    title_default = "Quote ready"
    if summary:
        title_default = f"Quote ready: {summary}"
    title = ctx.t("quote_ready.title", title_default).format(summary=summary or "your print job")

    body = [segment(f"Quote #{quote_id}", bold=True), segment(" is ready for review.")]
    if total is not None:
        body.append(segment(" Total "))
        body.append(segment(_money(total, currency), bold=True))
    if hours:
        body.append(segment(f", valid for {hours} hours."))
    return {
        "title": title,
        "body": body,
        "action_url": ctx.target_url,
        "action_label": ctx.t("quote_ready.action", "Review & pay"),
        "priority": Notification.PRIORITY_ACTION_REQUIRED,
    }


def _render_quote_updated(ctx: RenderContext) -> dict[str, Any] | None:
    base = _render_quote_ready(ctx)
    if base is None:
        return None
    base["title"] = ctx.t("quote_updated.title", "Quote updated")
    body = [segment("The quote was revised. ", bold=False)]
    body.extend(base["body"])
    base["body"] = body
    return base


def _render_quote_sent_notice(ctx: RenderContext) -> dict[str, Any] | None:
    request_id = ctx.param("request_id") or ctx.notification.object_id
    partner = ctx.param("partner_name") or "your print partner"
    return {
        "title": ctx.t("request_sent.title", "Request sent"),
        "body": [
            segment(f"Request #{request_id}", bold=True),
            segment(f" was sent to {partner}." if not _is_placeholder(partner) else "."),
        ],
        "action_url": ctx.target_url,
        "action_label": ctx.t("view_request.action", "View request"),
        "priority": Notification.PRIORITY_INFORMATIONAL,
    }


def _is_placeholder(value: Any) -> bool:
    return value in (None, "", "your print partner")


def _render_new_request(ctx: RenderContext) -> dict[str, Any] | None:
    request_id = ctx.param("request_id") or ctx.notification.object_id
    client_name = ctx.param("client_name") or _client_name(ctx.entity)
    body = [segment(f"Request #{request_id}", bold=True)]
    if client_name and ctx.viewer in {VIEWER_SHOP, VIEWER_MANAGER, VIEWER_ADMIN}:
        body.append(segment(f" from {client_name}"))
    body.append(segment(". Review and send a quote."))
    return {
        "title": ctx.t("new_request.title", "New quote request"),
        "body": body,
        "action_url": ctx.target_url,
        "action_label": ctx.t("review_request.action", "Review request"),
        "priority": Notification.PRIORITY_ACTION_REQUIRED,
    }


def _render_question(ctx: RenderContext) -> dict[str, Any] | None:
    text = ctx.param("body") or ctx.notification.message or "Your print partner needs a clarification."
    return {
        "title": ctx.t("question.title", "Question from your print partner"),
        "body": [segment(text)],
        "action_url": ctx.target_url,
        "action_label": ctx.t("reply.action", "Reply"),
        "priority": Notification.PRIORITY_ACTION_REQUIRED,
    }


def _render_clarification(ctx: RenderContext) -> dict[str, Any] | None:
    request_id = ctx.param("request_id") or ctx.notification.object_id
    client_name = ctx.param("client_name") or _client_name(ctx.entity) or "The client"
    return {
        "title": ctx.t("clarification.title", "Client replied"),
        "body": [segment(client_name, bold=True), segment(f" replied to request #{request_id}.")],
        "action_url": ctx.target_url,
        "action_label": ctx.t("view_reply.action", "View reply"),
        "priority": Notification.PRIORITY_ACTION_REQUIRED,
    }


def _render_quote_accepted(ctx: RenderContext) -> dict[str, Any] | None:
    request_id = ctx.param("request_id") or _request_id_from_quote(ctx.entity) or ctx.notification.object_id
    return {
        "title": ctx.t("quote_accepted.title", "Quote accepted"),
        "body": [segment(f"Request #{request_id}", bold=True), segment(" was accepted. Production can be prepared.")],
        "action_url": ctx.target_url,
        "action_label": ctx.t("open_job.action", "Open job"),
        "priority": Notification.PRIORITY_ACTION_REQUIRED,
    }


def _render_request_declined(ctx: RenderContext) -> dict[str, Any] | None:
    return {
        "title": ctx.t("request_declined.title", "Request can't be quoted"),
        "body": [segment(ctx.notification.message or "The request was declined.")],
        "action_url": ctx.target_url,
        "action_label": ctx.t("view_request.action", "View request"),
        "priority": Notification.PRIORITY_INFORMATIONAL,
    }


def _render_request_cancelled(ctx: RenderContext) -> dict[str, Any] | None:
    request_id = ctx.param("request_id") or ctx.notification.object_id
    return {
        "title": ctx.t("request_cancelled.title", "Request cancelled"),
        "body": [segment(f"Request #{request_id}", bold=True), segment(" was cancelled.")],
        "action_url": ctx.target_url,
        "action_label": None,
        "priority": Notification.PRIORITY_INFORMATIONAL,
    }


def _render_payment_received(ctx: RenderContext) -> dict[str, Any] | None:
    amount = ctx.param("amount")
    currency = ctx.param("currency") or CURRENCY_CODE
    job_title = ctx.param("job_title") or _job_title(ctx.entity)
    body = [segment("We've received ")]
    if amount is not None:
        body.append(segment(_money(amount, currency), bold=True))
        body.append(segment(f" for {job_title}. Production can start."))
    else:
        body.append(segment(f"your payment for {job_title}. Production can start."))
    return {
        "title": ctx.t("payment_received.title", "Payment received"),
        "body": body,
        "action_url": ctx.target_url,
        "action_label": ctx.t("track_job.action", "Track job"),
        "priority": Notification.PRIORITY_INFORMATIONAL,
    }


def _render_artwork_needed(ctx: RenderContext) -> dict[str, Any] | None:
    job_title = ctx.param("job_title") or _job_title(ctx.entity)
    return {
        "title": ctx.t("artwork_needed.title", "Artwork needed"),
        "body": [segment(f"Upload print-ready artwork for {job_title} to keep it on schedule.")],
        "action_url": ctx.target_url,
        "action_label": ctx.t("artwork_needed.action", "Upload artwork"),
        "priority": Notification.PRIORITY_ACTION_REQUIRED,
    }


def _render_ready_to_start(ctx: RenderContext) -> dict[str, Any] | None:
    job_title = ctx.param("job_title") or _job_title(ctx.entity)
    return {
        "title": ctx.t("ready_to_start.title", "Ready to start"),
        "body": [segment(f"{job_title} is paid and artwork is attached. You can start production.")],
        "action_url": ctx.target_url,
        "action_label": ctx.t("open_job.action", "Open job"),
        "priority": Notification.PRIORITY_ACTION_REQUIRED,
    }


def _render_job_status(ctx: RenderContext) -> dict[str, Any] | None:
    status = ctx.param("status_label") or ctx.param("status")
    title = f"Job update: {status}" if status else "Job update"
    return {
        "title": ctx.t("job_update.title", title),
        "body": [segment(ctx.notification.message or "The job status changed.")],
        "action_url": ctx.target_url,
        "action_label": ctx.t("track_job.action", "Track job"),
        "priority": Notification.PRIORITY_INFORMATIONAL,
    }


def _render_job_created(ctx: RenderContext) -> dict[str, Any] | None:
    return {
        "title": ctx.t("job_created.title", "Job created"),
        "body": [segment(ctx.notification.message or "A new job was created.")],
        "action_url": ctx.target_url,
        "action_label": ctx.t("open_job.action", "Open job"),
        "priority": Notification.PRIORITY_INFORMATIONAL,
    }


TEMPLATES: dict[str, TemplateFn] = {
    "quote_ready": _render_quote_ready,
    Notification.SHOP_QUOTE_SENT: _render_quote_ready,
    Notification.SHOP_QUOTE_REVISED: _render_quote_updated,
    Notification.QUOTE_REQUEST_SENT: _render_quote_sent_notice,
    Notification.QUOTE_REQUEST_SUBMITTED: _render_new_request,
    Notification.SHOP_QUESTION_ASKED: _render_question,
    Notification.BUYER_CLARIFICATION_SENT: _render_clarification,
    Notification.SHOP_QUOTE_ACCEPTED: _render_quote_accepted,
    Notification.REQUEST_DECLINED: _render_request_declined,
    Notification.QUOTE_REQUEST_CANCELLED: _render_request_cancelled,
    Notification.PAYMENT_CONFIRMED: _render_payment_received,
    Notification.ARTWORK_REQUIRED: _render_artwork_needed,
    Notification.JOB_READY_TO_START: _render_ready_to_start,
    Notification.JOB_STATUS_UPDATED: _render_job_status,
    Notification.JOB_CREATED: _render_job_created,
}


def _client_name(entity: Any) -> str:
    if entity is None:
        return ""
    quote_request = entity if entity.__class__.__name__ == "QuoteRequest" else getattr(entity, "quote_request", None)
    name = getattr(quote_request, "customer_name", "") if quote_request else ""
    if not name:
        user = getattr(quote_request, "created_by", None) if quote_request else None
        name = getattr(user, "name", "") or getattr(user, "email", "") if user else ""
    return name or ""


def _job_title(entity: Any) -> str:
    if entity is None:
        return "your print job"
    return getattr(entity, "title", "") or getattr(entity, "managed_reference", "") or f"job #{getattr(entity, 'id', '')}"


def _request_id_from_quote(entity: Any) -> Any:
    quote_request = getattr(entity, "quote_request", None)
    return getattr(quote_request, "id", None)


def render_notification(
    notification: Notification,
    *,
    user: Any = None,
    lang: str | None = None,
) -> dict[str, Any]:
    """Render a notification to title/body/action/priority for one recipient."""
    recipient = user if user is not None else getattr(notification, "user", None)
    lang = lang or getattr(recipient, "preferred_language", "") or "en"
    target_url = resolve_target_url(notification)
    viewer = resolve_entity_role(notification, recipient)
    params = notification.params if isinstance(notification.params, dict) else {}

    key = notification.template_key or notification.notification_type
    fallback_title = notification.get_notification_type_display()
    fallback = {
        "title": fallback_title,
        "body": _decorate([segment(notification.message or fallback_title)]),
        "action_url": target_url,
        "action_label": None,
        "priority": notification.priority or Notification.PRIORITY_INFORMATIONAL,
    }

    template = TEMPLATES.get(key)
    if template is None and notification.template_key:
        template = TEMPLATES.get(notification.notification_type)
    if template is None:
        return fallback

    ctx = RenderContext(
        notification=notification,
        user=recipient,
        lang=lang,
        viewer=viewer,
        params=params,
        entity=load_entity(notification),
        target_url=target_url,
    )
    try:
        rendered = template(ctx)
    except Exception as exc:  # noqa: BLE001 - rendering must never 500 the API
        logger.warning("Notification template %s failed: %s", key, exc)
        return fallback

    if not rendered:
        return fallback

    body = _decorate(rendered.get("body"))
    if not body:
        return fallback

    return {
        "title": rendered.get("title") or fallback_title,
        "body": body,
        "action_url": rendered.get("action_url", target_url),
        "action_label": rendered.get("action_label"),
        "priority": rendered.get("priority") or fallback["priority"],
    }
