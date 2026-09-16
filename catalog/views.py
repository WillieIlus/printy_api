from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response


class CatalogBrowseView(APIView):
    """Public catalog browsing - no authentication required."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"message": "Public catalog browsing area"})
