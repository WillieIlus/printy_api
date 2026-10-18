"""Anonymous pooled price-range endpoint (law 10.3).

Only the aggregate range is returned. The caller supplies the spec fingerprint;
it cannot ask for a shop, a job, or another shop's price, and no identity is
included in the response.
"""

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from quotes.public_ranges import build_public_price_range


class PublicPriceRangeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        spec_fingerprint = str(request.query_params.get("spec_fingerprint") or "").strip()
        price_range = build_public_price_range(spec_fingerprint)
        return Response({"range": price_range})
