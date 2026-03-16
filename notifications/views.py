"""Notification API views."""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

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
