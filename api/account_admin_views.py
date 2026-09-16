from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User, UserProfile

from .permissions import IsSuperUser


class BrokerProfileActiveToggleView(APIView):
    """Admin-only broker/partner active flag toggle."""

    permission_classes = [IsAuthenticated, IsSuperUser]

    def post(self, request, user_id):
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            return Response({"detail": "User not found."}, status=404)
        profile, _created = UserProfile.objects.get_or_create(user=user)
        raw_active = request.data.get("is_active")
        if not isinstance(raw_active, bool):
            return Response({"is_active": "Boolean value is required."}, status=400)
        profile.broker_profile_active = raw_active
        profile.save(update_fields=["broker_profile_active", "updated_at"])
        return Response(
            {
                "user_id": user.id,
                "email": user.email,
                "broker_profile_active": profile.broker_profile_active,
            }
        )
