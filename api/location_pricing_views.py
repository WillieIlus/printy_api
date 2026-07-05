from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from shops.location_pricing import get_location_pricing


class LocationPricingView(APIView):
    """
    GET /api/shops/location-pricing/?location=Nairobi
    Returns anonymous aggregated market pricing for the given city/area.
    No auth required — data is fully anonymised (no shop identities exposed).
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        location = request.query_params.get("location", "Nairobi").strip()
        if not location:
            return Response({"detail": "location parameter is required."}, status=400)

        fallback = request.query_params.get("fallback_to_city", "true").lower() != "false"
        data = get_location_pricing(location, fallback_to_city=fallback)
        return Response(data)
