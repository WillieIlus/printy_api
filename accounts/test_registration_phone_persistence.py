from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User, UserProfile


@override_settings(ACCOUNT_EMAIL_VERIFICATION="none")
class RegistrationPhonePersistenceTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_registration_persists_normalized_phone_to_profile(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "phone-persisted@test.com",
                "password": "Pass12345",
                "name": "Phone Persisted",
                "role": "client",
                "phone": "+254 733 123 456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="phone-persisted@test.com")
        self.assertEqual(user.role, User.Role.CLIENT)
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.phone, "254733123456")

    def test_registration_rejects_invalid_phone(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "bad-phone@test.com",
                "password": "Pass12345",
                "name": "Bad Phone",
                "role": "client",
                "phone": "12345",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.json().get("field_errors", {}))
        self.assertFalse(User.objects.filter(email="bad-phone@test.com").exists())

    def test_profile_phone_surfaces_through_me_endpoint(self):
        self.client.post(
            "/api/auth/register/",
            {
                "email": "phone-me@test.com",
                "password": "Pass12345",
                "name": "Phone Me",
                "role": "client",
                "phone": "0711 222 333",
            },
            format="json",
        )
        user = User.objects.get(email="phone-me@test.com")
        self.client.force_authenticate(user)

        me_response = self.client.get("/api/users/me/")

        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["phone"], "254711222333")