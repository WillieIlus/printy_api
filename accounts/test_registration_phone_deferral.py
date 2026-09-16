from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from accounts.services.roles import resolve_user_roles


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
    def test_manager_registration_succeeds_without_phone_and_stays_partner(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "manager-phone-deferred@test.com",
                "password": "Pass12345",
                "name": "Manager Phone Deferred",
                "role": "partner",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="manager-phone-deferred@test.com")
        self.assertEqual(user.role, User.Role.PARTNER)
        self.assertTrue(user.partner_profile_enabled)
        self.assertEqual(resolve_user_roles(user), ["partner"])
        self.assertFalse(UserProfile.objects.filter(user=user, phone__gt="").exists())

        self.client.force_authenticate(user)
        me_response = self.client.get("/api/users/me/")
        self.assertEqual(me_response.status_code, 200)
        payload = me_response.json()
        self.assertEqual(payload["role"], "partner")
        self.assertEqual(payload["primary_role"], "partner")
        self.assertEqual(payload["home_route"], "/dashboard/partner")


