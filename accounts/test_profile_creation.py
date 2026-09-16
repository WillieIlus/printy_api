from django.test import TestCase

from .models import User, UserProfile
from .serializers import UserSerializer, get_or_create_profile


class UserProfileCreationTestCase(TestCase):
    def test_get_or_create_profile_creates_default_profile(self):
        user = User.objects.create_user(
            email="profile-create@test.com",
            password="Pass12345",
            name="Profile Create",
        )

        profile = get_or_create_profile(user)

        self.assertEqual(profile.user, user)
        self.assertEqual(profile.default_markup_rate, 0)

    def test_user_serializer_lazy_creates_missing_profile(self):
        user = User.objects.create_user(
            email="serializer-profile@test.com",
            password="Pass12345",
            name="Serializer Profile",
        )

        payload = UserSerializer(user).data

        self.assertEqual(payload["email"], user.email)
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
