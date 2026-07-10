from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop


class ActiveRoleContextAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="hybrid-role@test.com",
            password="pass12345",
            name="Hybrid Role",
            role=User.Role.CLIENT,
            partner_profile_enabled=True,
        )
        Shop.objects.create(name="Hybrid Print", slug="hybrid-print", owner=self.user)
        self.user.role = User.Role.CLIENT
        self.user.save(update_fields=["role", "updated_at"])
        self.client.force_authenticate(user=self.user)

    def test_users_me_honors_valid_active_role_header_for_hybrid_accounts(self):
        response = self.client.get("/api/users/me/", HTTP_X_PRINTY_ACTIVE_ROLE="client")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["role"], "production")
        self.assertEqual(payload["roles"], ["production", "partner", "client"])
        self.assertEqual(payload["primary_role"], "production")
        self.assertEqual(payload["active_role"], "client")
        self.assertEqual(payload["active_dashboard_role"], "client")
        self.assertEqual(payload["dashboard_role"], "client")
        self.assertEqual(payload["home_route"], "/dashboard/client")

    def test_users_me_ignores_unavailable_active_role_header(self):
        response = self.client.get("/api/users/me/", HTTP_X_PRINTY_ACTIVE_ROLE="super_admin")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["primary_role"], "production")
        self.assertEqual(payload["active_role"], "production")
        self.assertEqual(payload["dashboard_role"], "production")
        self.assertEqual(payload["home_route"], "/dashboard/production")
