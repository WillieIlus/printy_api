from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import get_setup_status


class SetupStatusView(APIView):
    """
    GET /api/setup/status/
    Returns onboarding completion status and next step for the authenticated printer.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = get_setup_status(request.user)
        return Response(data)
