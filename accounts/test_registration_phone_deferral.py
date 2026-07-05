from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User, UserProfile


@override_settings(ACCOUNT_EMAIL_VERIFICATION="none")
class RegistrationPhoneDeferralTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_registration_succeeds_without_phone(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "phone-deferred@test.com",
                "password": "Pass12345",
                "name": "Phone Deferred",
                "role": "client",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="phone-deferred@test.com")
        self.assertEqual(user.role, User.Role.CLIENT)
        self.assertFalse(UserProfile.objects.filter(user=user, phone__gt="").exists())
