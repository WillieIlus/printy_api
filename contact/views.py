"""Public contact-form submission endpoint."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ContactMessageSerializer


class ContactSubmissionView(APIView):
    """POST /api/contact/submit/ — persist a message for staff review.

    AllowAny because the contact page is public and pre-auth. The scoped
    throttle keeps a script from filling the inbox.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "contact_submit"

    def post(self, request):
        serializer = ContactMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.save(
            user=request.user if getattr(request.user, "is_authenticated", False) else None
        )
        return Response(ContactMessageSerializer(message).data, status=status.HTTP_201_CREATED)