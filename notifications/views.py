"""Notification API views."""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from shops.models import Shop

from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(ReadOnlyModelViewSet):
    """
    In-app notifications for the current user.
    GET /me/notifications/ — list (newest first)
    GET /me/notifications/{id}/ — retrieve
    POST /me/notifications/{id}/mark-read/ — mark as read
    POST /me/notifications/mark-all-read/ — mark all as read
    """

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).select_related("actor")

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        n = self.get_object()
        if not n.read_at:
            n.read_at = timezone.now()
            n.save(update_fields=["read_at"])
        return Response(NotificationSerializer(n).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated = Notification.objects.filter(
            user=request.user, read_at__isnull=True
        ).update(read_at=timezone.now())
        return Response({"marked": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = Notification.objects.filter(
            user=request.user, read_at__isnull=True
        ).count()
        return Response({"count": count})

    @action(detail=False, methods=["get"], url_path="activity-summary")
    def activity_summary(self, request):
        shop_slug = request.query_params.get("shop_slug", "").strip()
        unread_notifications = Notification.objects.filter(
            user=request.user,
            read_at__isnull=True,
            object_type="quote_request",
        )

        shop_queryset = Shop.objects.filter(owner=request.user, is_active=True)
        if shop_slug:
            shop_queryset = shop_queryset.filter(slug=shop_slug)

        shop_requests = QuoteRequest.objects.filter(shop__in=shop_queryset)
        submitted_shop_request_ids = list(
            shop_requests.filter(status=QuoteStatus.SUBMITTED).values_list("id", flat=True)
        )
        awaiting_shop_action_ids = list(
            shop_requests.filter(status=QuoteStatus.AWAITING_SHOP_ACTION).values_list("id", flat=True)
        )

        my_requests = QuoteRequest.objects.filter(created_by=request.user)
        awaiting_client_reply_ids = list(
            my_requests.filter(status=QuoteStatus.AWAITING_CLIENT_REPLY).values_list("id", flat=True)
        )
        my_request_ids = list(my_requests.values_list("id", flat=True))

        shop_unread_request_notifications = unread_notifications.filter(
            notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
            object_id__in=submitted_shop_request_ids + awaiting_shop_action_ids,
        )

        client_unread_request_notifications = unread_notifications.filter(
            object_id__in=my_request_ids,
        )

        payload = {
            "shop": {
                "incoming_requests": shop_unread_request_notifications.filter(
                    object_id__in=submitted_shop_request_ids
                ).values("object_id").distinct().count(),
                "messages_replies": shop_unread_request_notifications.filter(
                    object_id__in=awaiting_shop_action_ids
                ).values("object_id").distinct().count(),
                "pending_quote_actions": shop_requests.filter(
                    status__in=[
                        QuoteStatus.SUBMITTED,
                        QuoteStatus.VIEWED,
                        QuoteStatus.ACCEPTED,
                        QuoteStatus.AWAITING_SHOP_ACTION,
                    ]
                ).count(),
            },
            "client": {
                "new_quotes": client_unread_request_notifications.filter(
                    notification_type__in=[
                        Notification.SHOP_QUOTE_SENT,
                        Notification.SHOP_QUOTE_REVISED,
                    ]
                ).values("object_id").distinct().count(),
                "shop_replies": client_unread_request_notifications.filter(
                    notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
                    object_id__in=awaiting_client_reply_ids,
                ).values("object_id").distinct().count(),
                "request_updates": client_unread_request_notifications.filter(
                    notification_type__in=[
                        Notification.REQUEST_DECLINED,
                        Notification.QUOTE_REQUEST_CANCELLED,
                    ]
                ).values("object_id").distinct().count(),
            },
            "notifications": {
                "unread_total": unread_notifications.count(),
            },
        }
        return Response(payload)
