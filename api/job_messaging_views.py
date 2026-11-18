"""API views for stage-scoped managed-job messages."""

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from jobs.messaging import (
    create_job_message,
    job_for_user,
    job_message_bundle,
    mark_message_read,
)
from quotes.models import QuoteRequestMessage


class ManagedJobMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        job = job_for_user(pk, request.user)
        return Response(job_message_bundle(job, request.user, request=request))

    def post(self, request, pk):
        job = job_for_user(pk, request.user)
        body = str(request.data.get("body") or "").strip()
        if not body:
            return Response({"body": ["Write a message first."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            message = create_job_message(
                job=job,
                user=request.user,
                body=body,
                stage_key=str(request.data.get("stage") or ""),
                requested_audience=str(request.data.get("audience") or ""),
            )
        except ValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        bundle = job_message_bundle(job, request.user, request=request)
        bundle["created_message_id"] = message.id
        return Response(bundle, status=status.HTTP_201_CREATED)


class ManagedJobMessageReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, message_id):
        job = job_for_user(pk, request.user)
        bundle = job_message_bundle(job, request.user, request=request)
        visible_ids = {
            message["id"]
            for stage in bundle["stages"]
            for message in stage["messages"]
        }
        if message_id not in visible_ids:
            return Response({"detail": "Message not found for this job."}, status=status.HTTP_404_NOT_FOUND)
        message = QuoteRequestMessage.objects.filter(pk=message_id, managed_job=job).first()
        if message is None:
            return Response({"detail": "Message not found for this job."}, status=status.HTTP_404_NOT_FOUND)
        mark_message_read(message, request.user)
        return Response(job_message_bundle(job, request.user, request=request))

