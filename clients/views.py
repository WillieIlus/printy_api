"""HTTP surface for the managed-client flow (law 10.4-10.6).

Manager endpoints (active role ``partner``):

* create a quote request on behalf of a client,
* create/replace a signed single-job client link and deliver it,
* revoke a link.

Public endpoints (rate-limited, ``AllowAny``):

* open a link (buyer projection only),
* request and verify an OTP,
* perform the three OTP-gated actions: artwork upload, price/date
  acknowledgement and payment initiation.
"""
from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.phone import normalize_phone
from accounts.services.roles import (
    CANONICAL_PARTNER_ROLE,
    ActiveRoleNotHeld,
    get_request_active_role_raw,
    validate_active_role,
)
from clients.models import ClientLink, MessageDelivery
from clients.serializers import (
    CreateRequestForClientSerializer,
    OtpRequestSerializer,
    OtpVerifySerializer,
    build_link_payload,
)
from clients.services import links as link_service
from clients.services.managed_clients import (
    ManagedClientError,
    create_request_for_client,
    log_client_event,
)
from clients.services.messaging import send_client_message
from clients.services.otp import (
    OtpError,
    OtpLocked,
    OtpThrottled,
    request_otp,
    validate_action_token,
    verify_otp,
)
from clients.throttling import (
    ClientLinkViewThrottle,
    ClientOtpRequestThrottle,
    ClientOtpVerifyThrottle,
)
from quotes.models import QuoteRequest


def _client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class IsManagerActor(BasePermission):
    """Only an account acting as a manager (active role ``partner``)."""

    message = "Only a manager can do this."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        try:
            active_role = validate_active_role(user, get_request_active_role_raw(request))
        except ActiveRoleNotHeld as exc:
            self.message = str(exc)
            return False
        if active_role != CANONICAL_PARTNER_ROLE:
            self.message = "Only a manager can do this."
            return False
        return True


def _manager_requests(user):
    return QuoteRequest.objects.filter(
        Q(created_by_manager=user) | Q(created_by=user) | Q(assigned_manager=user)
    )


def _link_client_phone(link: ClientLink) -> str:
    try:
        return normalize_phone(link.quote_request.customer_phone)
    except Exception:
        return ""


def _delivery_payload(delivery) -> dict | None:
    if delivery is None:
        return None
    return {
        "id": delivery.id,
        "status": delivery.status,
        "channel": delivery.channel,
        "provider": delivery.provider,
    }


def _otp_token_from_request(request) -> str:
    return str(
        request.data.get("otp_token")
        or request.query_params.get("otp_token")
        or request.headers.get("X-Printy-Otp")
        or ""
    ).strip()


class ManagerCreateClientRequestView(APIView):
    """Law 10.5: create a quote request on behalf of a client."""

    permission_classes = [IsAuthenticated, IsManagerActor]

    def post(self, request):
        serializer = CreateRequestForClientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            quote_request, client_user, invite = create_request_for_client(
                manager=request.user,
                name=data["name"],
                phone=data["phone"],
                contact_permitted=data["contact_permitted"],
                email=data.get("email", ""),
                company=data.get("company", ""),
                details=data.get("details", {}),
                notes=data.get("notes", ""),
                delivery_preference=data.get("delivery_preference", ""),
            )
        except ManagedClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        invite_payload = None
        if invite is not None:
            from accounts.services.client_invites import build_invite_claim_url

            invite_payload = {
                "id": invite.id,
                "claim_token": invite.token,
                "claim_url": build_invite_claim_url(invite),
                "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
            }

        from jobs.job_codes import quote_request_code

        return Response(
            {
                "quote_request_id": quote_request.id,
                "request_reference": quote_request.request_reference,
                "code": quote_request_code(quote_request),
                "client_id": getattr(client_user, "id", None),
                "on_behalf_of": quote_request.on_behalf_of_id,
                "created_by_manager": quote_request.created_by_manager_id,
                "client_contact_permitted": True,
                "client_name": quote_request.customer_name,
                "client_phone": quote_request.customer_phone,
                "status": quote_request.status,
                "invite": invite_payload,
            },
            status=status.HTTP_201_CREATED,
        )


class ManagerCreateClientLinkView(APIView):
    """Law 10.6: create (and replace) a signed single-job link and deliver it.

    ``GET`` reports the current link's status (sent/viewed/expired/revoked) so
    the manager UI can show it without guessing; ``POST`` creates/replaces the
    link and delivers it.
    """

    permission_classes = [IsAuthenticated, IsManagerActor]

    def get(self, request, pk):
        quote_request = _manager_requests(request.user).filter(pk=pk).first()
        if quote_request is None:
            return Response({"detail": "Quote request not found."}, status=status.HTTP_404_NOT_FOUND)

        link = link_service.latest_link_for(quote_request)
        delivery = quote_request.message_deliveries.order_by("-created_at", "-id").first()
        return Response(
            {
                "quote_request_id": quote_request.id,
                "has_link": link is not None,
                "link_id": link.id if link is not None else None,
                "token": link.token if link is not None else None,
                "url": link.url if link is not None else None,
                "status": link_service.link_status(link),
                "sent_at": link.created_at.isoformat() if link is not None else None,
                "viewed_at": link.last_used_at.isoformat() if link is not None and link.last_used_at else None,
                "view_count": link.use_count if link is not None else 0,
                "expires_at": link.expires_at.isoformat() if link is not None else None,
                "is_active": bool(link.is_active_now) if link is not None else False,
                "delivery": _delivery_payload(delivery),
            }
        )

    def post(self, request, pk):
        quote_request = _manager_requests(request.user).filter(pk=pk).first()
        if quote_request is None:
            return Response({"detail": "Quote request not found."}, status=status.HTTP_404_NOT_FOUND)

        link = link_service.create_client_link(quote_request=quote_request, manager=request.user)

        delivery = None
        channel = str(request.data.get("channel") or MessageDelivery.Channel.SMS).strip().lower()
        if channel not in {MessageDelivery.Channel.SMS, MessageDelivery.Channel.WHATSAPP}:
            channel = MessageDelivery.Channel.SMS
        if _link_client_phone(link):
            from jobs.job_codes import quote_request_code

            delivery = send_client_message(
                recipient=quote_request.customer_phone,
                message=f"Track or pay for {quote_request_code(quote_request)}: {link.url}",
                channel=channel,
                manager_name=getattr(request.user, "name", ""),
                quote_request=quote_request,
                client=quote_request.on_behalf_of,
            )

        log_client_event(
            quote_request,
            event="client_link_created",
            actor=request.user,
            link_id=link.id,
            channel=channel,
        )

        return Response(
            {
                "link_id": link.id,
                "token": link.token,
                "url": link.url,
                "expires_at": link.expires_at.isoformat(),
                "delivery": _delivery_payload(delivery),
            },
            status=status.HTTP_201_CREATED,
        )


class ManagerRevokeClientLinkView(APIView):
    """Law 10.6: a manager may revoke a link."""

    permission_classes = [IsAuthenticated, IsManagerActor]

    def post(self, request, pk):
        link = (
            ClientLink.objects.filter(pk=pk)
            .filter(quote_request__in=_manager_requests(request.user))
            .select_related("quote_request")
            .first()
        )
        if link is None:
            return Response({"detail": "Link not found."}, status=status.HTTP_404_NOT_FOUND)
        link_service.revoke_client_link(link, manager=request.user)
        log_client_event(quote_request=link.quote_request, event="client_link_revoked", actor=request.user, link_id=link.id)
        return Response({"detail": "Link revoked.", "link_id": link.id, "revoked_at": link.revoked_at.isoformat()})


class ClientLinkView(APIView):
    """Law 10.6: view the one quote/job a link opens (buyer projection only)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ClientLinkViewThrottle]

    def get(self, request, token):
        link = link_service.resolve_active_link(token)
        if link is None:
            return Response({"detail": "This link is invalid, expired or has been replaced."}, status=status.HTTP_404_NOT_FOUND)

        link_service.record_link_use(
            link,
            ip_address=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return Response(build_link_payload(link.quote_request))


class ClientLinkOtpRequestView(APIView):
    """Law 10.4: send an OTP before any action on the link."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ClientOtpRequestThrottle]

    def post(self, request, token):
        link = link_service.resolve_active_link(token)
        if link is None:
            return Response({"detail": "This link is invalid, expired or has been replaced."}, status=status.HTTP_404_NOT_FOUND)

        serializer = OtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        quote_request = link.quote_request
        phone = _link_client_phone(link)
        if not phone:
            return Response({"detail": "This link has no phone number on the job."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            challenge, code = request_otp(phone_normalized=phone, quote_request=quote_request)
        except OtpThrottled as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except OtpError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        send_client_message(
            recipient=phone,
            message=f"Your Printy verification code is {code}.",
            manager_name=getattr(quote_request.created_by_manager, "name", ""),
            quote_request=quote_request,
            client=quote_request.on_behalf_of,
        )

        body = {
            "detail": "We sent a verification code to your phone.",
            "expires_at": challenge.expires_at.isoformat(),
            "resend_cooldown_seconds": int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 60)),
        }
        # Test seam only: the plaintext code is never echoed in production.
        if getattr(settings, "CLIENT_OTP_ECHO", False):
            body["code"] = code
        return Response(body)


class ClientLinkOtpVerifyView(APIView):
    """Law 10.4: verify the OTP and return the action token."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ClientOtpVerifyThrottle]

    def post(self, request, token):
        link = link_service.resolve_active_link(token)
        if link is None:
            return Response({"detail": "This link is invalid, expired or has been replaced."}, status=status.HTTP_404_NOT_FOUND)

        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        quote_request = link.quote_request
        phone = _link_client_phone(link)
        if not phone:
            return Response({"detail": "This link has no phone number on the job."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            challenge = verify_otp(
                phone_normalized=phone,
                code=serializer.validated_data["code"],
                quote_request=quote_request,
            )
        except OtpLocked as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except OtpError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "detail": "Phone verified.",
                "otp_token": challenge.verified_token,
                "expires_at": challenge.expires_at.isoformat(),
            }
        )


class ClientLinkActionView(APIView):
    """Base for the three OTP-gated client actions (law 10.4)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def _authorized_link(self, request, token):
        link = link_service.resolve_active_link(token)
        if link is None:
            return None, Response(
                {"detail": "This link is invalid, expired or has been replaced."},
                status=status.HTTP_404_NOT_FOUND,
            )
        phone = _link_client_phone(link)
        if not validate_action_token(_otp_token_from_request(request), quote_request=link.quote_request, phone_normalized=phone):
            return None, Response(
                {"detail": "Verify your phone with the code we sent before continuing."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return link, None


class ClientLinkArtworkView(ClientLinkActionView):
    """OTP-gated artwork upload (law 10.4)."""

    def post(self, request, token):
        link, error = self._authorized_link(request, token)
        if error is not None:
            return error
        upload = request.FILES.get("file")
        filename = getattr(upload, "name", None) or str(request.data.get("filename") or "").strip()
        size = getattr(upload, "size", None)
        log_client_event(
            link.quote_request,
            event="client_artwork_uploaded",
            filename=filename,
            size=size,
            via="client_link",
        )
        return Response(
            {"detail": "Artwork received.", "filename": filename, "size": size},
            status=status.HTTP_202_ACCEPTED,
        )


class ClientLinkAcknowledgeView(ClientLinkActionView):
    """OTP-gated price/date acknowledgement (law 10.4)."""

    def post(self, request, token):
        link, error = self._authorized_link(request, token)
        if error is not None:
            return error
        kind = str(request.data.get("acknowledge") or "both").strip().lower()
        if kind not in {"price", "date", "both"}:
            return Response({"detail": "acknowledge must be price, date or both."}, status=status.HTTP_400_BAD_REQUEST)

        quote_request = link.quote_request
        snapshot = quote_request.request_snapshot if isinstance(quote_request.request_snapshot, dict) else {}
        now = timezone.now().isoformat()
        if kind in {"price", "both"}:
            snapshot["price_acknowledged_at"] = now
        if kind in {"date", "both"}:
            snapshot["date_acknowledged_at"] = now
        quote_request.request_snapshot = snapshot
        quote_request.save(update_fields=["request_snapshot", "updated_at"])
        log_client_event(quote_request, event="client_acknowledged", kind=kind, via="client_link")

        return Response(
            {
                "detail": "Thank you — your acknowledgement is recorded.",
                "acknowledged": kind,
                "price_acknowledged_at": snapshot.get("price_acknowledged_at"),
                "date_acknowledged_at": snapshot.get("date_acknowledged_at"),
            }
        )


class ClientLinkPaymentView(ClientLinkActionView):
    """OTP-gated payment initiation (law 10.4).

    Creates the single Printy custody payment for the accepted quote. The
    Daraja STK request itself is unchanged and remains the caller's existing
    flow (law 10.7 is out of scope for this batch); this endpoint only enforces
    the OTP gate and records the initiation.
    """

    def post(self, request, token):
        link, error = self._authorized_link(request, token)
        if error is not None:
            return error

        from quotes.choices import QuoteStatus
        from quotes.models import Quote

        quote_request = link.quote_request
        quote = (
            Quote.objects.filter(quote_request=quote_request, status=QuoteStatus.ACCEPTED)
            .order_by("-id")
            .first()
        )
        if quote is None:
            return Response(
                {"detail": "This request has no accepted quote to pay yet."},
                status=status.HTTP_409_CONFLICT,
            )

        payer = quote_request.on_behalf_of or quote_request.created_by_manager or quote_request.created_by
        if payer is None:
            return Response({"detail": "No payer is linked to this request."}, status=status.HTTP_400_BAD_REQUEST)

        from payments.services import create_payment_for_quote

        try:
            payment = create_payment_for_quote(
                quote=quote,
                payer=payer,
                payer_phone=quote_request.customer_phone or "",
            )
        except Exception as exc:  # surfaces service validation messages as a 400
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        log_client_event(quote_request, event="client_payment_initiated", payment_id=payment.id, via="client_link")
        return Response(
            {
                "detail": "Payment initiated.",
                "payment_id": payment.id,
                "status": payment.status,
                "amount": str(payment.amount),
            },
            status=status.HTTP_201_CREATED,
        )
