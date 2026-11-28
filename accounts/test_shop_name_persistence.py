from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop


@override_settings(ACCOUNT_EMAIL_VERIFICATION="none")
class PrinterShopNamePersistenceTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _register_printer(self, *, email, shop_name="North Press Co.", **extra):
        """Printer applicant sign-up, then shop creation (audit #3 flow).

        Registration never grants production or creates a shop any more: the
        applicant signs up via /api/auth/register/production/ and creates the
        shop through POST /api/shops/ (private until an admin approves it).
        """
        payload = {
            "email": email,
            "password": "Pass12345",
            "name": "Press Operator",
            "shop_name": shop_name,
        }
        payload.update(extra)
        response = self.client.post("/api/auth/register/production/", payload, format="json")
        if response.status_code == 201 and shop_name and "role" not in extra:
            token = self._login(email)
            created = self.client.post(
                "/api/shops/",
                {"name": shop_name},
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )
            assert created.status_code == 201, created.content
        return response

    def _login(self, email, password="Pass12345"):
        response = self.client.post(
            "/api/auth/login/",
            {"email": email, "password": password},
            format="json",
        )
        return response.json()["access"]

    def test_printer_signup_persists_company_name_in_shop(self):
        response = self._register_printer(email="north@test.com", shop_name="North Press Co.")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json().get("signup_intent"), "printer_applicant")
        user = User.objects.get(email="north@test.com")
        shop = Shop.objects.get(owner=user)
        self.assertEqual(shop.name, "North Press Co.")
        # Self-created shops start private until an admin approves them.
        self.assertFalse(shop.is_public)

    def test_printer_signup_alone_creates_no_shop_and_no_production_role(self):
        response = self.client.post(
            "/api/auth/register/production/",
            {"email": "applicant@test.com", "password": "Pass12345", "shop_name": "Applicant Press"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(email="applicant@test.com")
        self.assertEqual(user.role, User.Role.CLIENT)
        self.assertFalse(Shop.objects.filter(owner=user).exists())

    def test_blank_company_name_does_not_create_shop(self):
        response = self._register_printer(email="blank-shop@test.com", shop_name="")

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="blank-shop@test.com")
        self.assertFalse(Shop.objects.filter(owner=user).exists())

    def test_non_production_signup_does_not_create_shop(self):
        response = self._register_printer(
            email="buyer@test.com",
            shop_name="Buyer Org",
            role="client",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="buyer@test.com")
        self.assertFalse(Shop.objects.filter(owner=user).exists())

    def test_current_user_response_contains_company_name(self):
        self._register_printer(email="me@test.com", shop_name="Me Press Co.")

        token = self._login("me@test.com")
        me_response = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["shop_name"], "Me Press Co.")

    def test_company_name_survives_fresh_session(self):
        self._register_printer(email="fresh@test.com", shop_name="Fresh Press Co.")

        first = self.client.get(
            "/api/auth/me/",
            HTTP_AUTHORIZATION=f"Bearer {self._login('fresh@test.com')}",
        )
        self.assertEqual(first.json()["shop_name"], "Fresh Press Co.")

        fresh_client = APIClient()
        token = fresh_client.post(
            "/api/auth/login/",
            {"email": "fresh@test.com", "password": "Pass12345"},
            format="json",
        ).json()["access"]
        second = fresh_client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["shop_name"], "Fresh Press Co.")

    def test_legacy_printer_without_shop_can_add_company_name(self):
        self._register_printer(email="legacy@test.com", shop_name="")

        user = User.objects.get(email="legacy@test.com")
        self.assertFalse(Shop.objects.filter(owner=user).exists())

        token = self._login("legacy@test.com")
        create_response = self.client.post(
            "/api/shops/",
            {"name": "Legacy Press Co."},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(create_response.status_code, 201, create_response.json())
        me_response = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(me_response.json()["shop_name"], "Legacy Press Co.")

    def test_updating_company_name_persists_to_canonical_field(self):
        self._register_printer(email="rename@test.com", shop_name="Old Press Co.")

        user = User.objects.get(email="rename@test.com")
        shop = Shop.objects.get(owner=user)
        token = self._login("rename@test.com")

        patch_response = self.client.patch(
            f"/api/shops/{shop.slug}/",
            {"name": "Renamed Press Co."},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(patch_response.status_code, 200, patch_response.json())
        shop.refresh_from_db()
        self.assertEqual(shop.name, "Renamed Press Co.")
        me_response = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(me_response.json()["shop_name"], "Renamed Press Co.")

    def test_auth_me_alias_also_contains_company_name(self):
        self._register_printer(email="alias@test.com", shop_name="Alias Press Co.")

        token = self._login("alias@test.com")
        response = self.client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["shop_name"], "Alias Press Co.")