from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shops.models import Shop

from .services import get_setup_status_for_shop, get_setup_status_for_user


class SetupStatusView(APIView):
    """
    GET /api/setup-status/
    Returns onboarding completion status and next step for the authenticated user.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = get_setup_status_for_user(request.user)
        return Response(data)


class ShopSetupStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        shop = Shop.objects.get(slug=slug)
        data = get_setup_status_for_shop(shop)
        return Response(data)
