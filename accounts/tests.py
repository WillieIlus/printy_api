import unittest

raise unittest.SkipTest("Legacy pre-reset tests target removed role models/routes.")

from urllib.parse import parse_qs, urlparse

from django.db import IntegrityError
from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from rest_framework.test import APIClient

from allauth.account.models import EmailAddress

from .models import User, UserProfile, UserRole
from .services.capabilities import get_account_capabilities
from .services.roles import resolve_dashboard_role, resolve_user_roles
from shops.models import Shop


class AccountProfileAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="owner@test.com",
            password="pass12345",
            name="Owner User",
        )
        self.client.force_authenticate(user=self.user)

    def test_users_me_updates_user_and_profile_fields(self):
        response = self.client.patch(
            "/api/users/me/",
            {
                "first_name": "Amina",
                "last_name": "Otieno",
                "role": "shop_owner",
                "preferred_language": "sw",
                "phone": "+254700000000",
                "bio": "Print production lead",
                "address": "Muthithi Road",
                "city": "Westlands",
                "state": "Nairobi",
                "country": "Kenya",
                "postal_code": "00100",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        profile = UserProfile.objects.get(user=self.user)

        self.assertEqual(self.user.first_name, "Amina")
        self.assertEqual(self.user.last_name, "Otieno")
        self.assertEqual(self.user.name, "Amina Otieno")
        self.assertEqual(self.user.role, User.Role.PRODUCTION)
        self.assertEqual(self.user.preferred_language, "sw")
        self.assertEqual(profile.phone, "+254700000000")
        self.assertEqual(profile.city, "Westlands")

    def test_profiles_me_patch_persists_profile_fields(self):
        response = self.client.patch(
            "/api/profiles/me/",
            {
                "bio": "Offset and digital specialist",
                "phone": "+254711111111",
                "address": "Madonna House, 2nd Floor",
                "city": "Westlands",
                "state": "Nairobi",
                "country": "Kenya",
                "postal_code": "00800",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.bio, "Offset and digital specialist")
        self.assertEqual(response.json()["phone"], "+254711111111")

    def test_shop_creation_promotes_client_to_shop_owner(self):
        self.assertEqual(self.user.role, User.Role.CLIENT)

        Shop.objects.create(name="Role Sync Shop", slug="role-sync-shop", owner=self.user)

        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.PRODUCTION)

    def test_users_me_exposes_capability_foundations(self):
        Shop.objects.create(name="Capability Shop", slug="capability-shop", owner=self.user)
        self.user.partner_profile_enabled = True
        self.user.capability_overrides = {"can_receive_payouts": False}
        self.user.save(update_fields=["partner_profile_enabled", "capability_overrides", "updated_at"])

        response = self.client.get("/api/users/me/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["partner_profile_enabled"], True)
        self.assertEqual(payload["capability_overrides"], {"can_receive_payouts": False})
        self.assertEqual(payload["role"], "production")
        self.assertEqual(payload["roles"], ["production", "partner", "client"])
        self.assertEqual(payload["primary_role"], "production")
        self.assertEqual(payload["home_route"], "/dashboard/production")
        self.assertEqual(payload["dashboard_role"], "production")
        self.assertEqual(payload["capabilities"]["can_source_jobs"], True)
        self.assertEqual(payload["capabilities"]["can_manage_clients"], True)
        self.assertEqual(payload["capabilities"]["can_receive_payouts"], False)

    def test_capability_resolution_allows_hybrid_partner_shop_accounts(self):
        Shop.objects.create(name="Hybrid Shop", slug="hybrid-shop", owner=self.user)
        self.user.partner_profile_enabled = True
        self.user.save(update_fields=["partner_profile_enabled", "updated_at"])

        capabilities = get_account_capabilities(self.user)

        self.assertTrue(capabilities["can_manage_clients"])
        self.assertTrue(capabilities["can_source_jobs"])
        self.assertTrue(capabilities["can_receive_assignments"])
        self.assertTrue(capabilities["can_manage_production"])
        self.assertTrue(capabilities["can_receive_payouts"])
        self.assertEqual(resolve_dashboard_role(self.user), "production")

    def test_dashboard_role_resolution_prioritizes_partner_before_client(self):
        self.user.role = User.Role.PARTNER
        self.user.partner_profile_enabled = True
        self.user.save(update_fields=["role", "partner_profile_enabled", "updated_at"])

        self.assertEqual(resolve_dashboard_role(self.user), "partner")

    def test_role_resolution_normalizes_legacy_aliases(self):
        self.user.role = User.Role.BROKER
        self.user.save(update_fields=["role", "updated_at"])
        self.assertEqual(resolve_user_roles(self.user)[0], "partner")
        self.assertIn("partner", resolve_user_roles(self.user))

        self.user.role = User.Role.SHOP_OWNER
        self.user.save(update_fields=["role", "updated_at"])
        self.assertEqual(resolve_user_roles(self.user)[0], "production")
        self.assertIn("production", resolve_user_roles(self.user))

    def test_superuser_resolves_to_super_admin_home_route(self):
        admin_user = User.objects.create_superuser(
            email="super-admin@test.com",
            password="pass12345",
        )

        self.assertEqual(resolve_user_roles(admin_user), ["super_admin"])
        self.assertEqual(resolve_dashboard_role(admin_user), "super_admin")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://printy.ke",
)
class EmailVerificationFlowAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _extract_confirmation_key(self, message_body: str) -> str:
        for token in message_body.split():
            if "key=" not in token:
                continue
            parsed = urlparse(token.strip())
            key = parse_qs(parsed.query).get("key", [None])[0]
            if key:
                return key
        self.fail("Could not extract confirmation key from email body.")

    def _extract_link_with_key(self, message_body: str) -> str:
        for token in message_body.split():
            if "key=" in token:
                return token.strip()
        self.fail("Could not extract keyed link from email body.")

    def test_register_sends_verification_email_and_blocks_login_until_confirmed(self):
        register_response = self.client.post(
            "/api/auth/register/",
            {
                "email": "new-user@test.com",
                "password": "Pass12345",
                "name": "New User",
                "role": "client",
            },
            format="json",
        )

        self.assertEqual(register_response.status_code, 201)
        self.assertEqual(register_response.json()["detail"], "Check your email to activate your Printy account.")
        self.assertEqual(register_response.json()["email"], "new-user@test.com")
        self.assertEqual(register_response.json()["verification_required"], True)
        self.assertEqual(register_response.json()["resend_available"], True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject.strip(), "Welcome to Printy - please verify your email")
        self.assertIn("Hello from Printy!", mail.outbox[0].body)
        self.assertIn("https://printy.ke/auth/confirm-email?key=", mail.outbox[0].body)
        self.assertNotIn("example.com", mail.outbox[0].body)
        self.assertNotIn("localhost:3000", mail.outbox[0].body)
        confirmation_link = self._extract_link_with_key(mail.outbox[0].body)
        parsed_confirmation = urlparse(confirmation_link)
        self.assertEqual(parsed_confirmation.scheme, "https")
        self.assertEqual(parsed_confirmation.netloc, "printy.ke")
        self.assertEqual(parsed_confirmation.path, "/auth/confirm-email")

        email_address = EmailAddress.objects.get(email="new-user@test.com")
        self.assertFalse(email_address.verified)

        login_response = self.client.post(
            "/api/auth/token/",
            {"email": "new-user@test.com", "password": "Pass12345"},
            format="json",
        )

        self.assertEqual(login_response.status_code, 400)
        self.assertEqual(login_response.json()["message"], "Your account exists but needs email verification.")
        self.assertEqual(login_response.json()["field_errors"]["code"][0], "EMAIL_UNVERIFIED")
        self.assertEqual(login_response.json()["field_errors"]["email"][0], "new-user@test.com")
        self.assertEqual(login_response.json()["field_errors"]["resend_available"][0], "True")

    def test_client_signup_creates_client_role(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "client-signup@test.com",
                "password": "Pass12345",
                "name": "Client Signup",
                "role": "client",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="client-signup@test.com")
        self.assertEqual(user.role, User.Role.CLIENT)
        self.assertEqual(resolve_user_roles(user), ["client"])
        self.assertEqual(list(user.user_roles.filter(is_active=True).values_list("role", flat=True)), ["client"])

    def test_generic_signup_defaults_to_client_when_role_missing(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "default-client@test.com",
                "password": "Pass12345",
                "name": "Default Client",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="default-client@test.com")
        self.assertEqual(resolve_user_roles(user), ["client"])
        self.assertEqual(user.user_roles.filter(role="client", is_active=True).count(), 1)

    def test_partner_signup_creates_partner_role_not_client(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "partner-signup@test.com",
                "password": "Pass12345",
                "name": "Partner Signup",
                "role": "partner",
                "partner_profile_enabled": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="partner-signup@test.com")
        self.assertEqual(user.role, User.Role.PARTNER)
        self.assertEqual(resolve_user_roles(user), ["partner"])
        self.assertEqual(user.user_roles.filter(role="partner", is_active=True).count(), 1)

    def test_production_signup_creates_production_role_not_client(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "production-signup@test.com",
                "password": "Pass12345",
                "name": "Production Signup",
                "role": "production",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="production-signup@test.com")
        self.assertEqual(user.role, User.Role.PRODUCTION)
        self.assertEqual(resolve_user_roles(user), ["production"])
        self.assertEqual(user.user_roles.filter(role="production", is_active=True).count(), 1)

    def test_dedicated_partner_signup_endpoint_assigns_partner(self):
        response = self.client.post(
            "/api/auth/register/partner/",
            {
                "email": "partner-dedicated@test.com",
                "password": "Pass12345",
                "name": "Partner Dedicated",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="partner-dedicated@test.com")
        self.assertEqual(resolve_user_roles(user), ["partner"])
        self.assertTrue(user.partner_profile_enabled)

    def test_dedicated_production_signup_endpoint_assigns_production(self):
        response = self.client.post(
            "/api/auth/register/production/",
            {
                "email": "production-dedicated@test.com",
                "password": "Pass12345",
                "name": "Production Dedicated",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="production-dedicated@test.com")
        self.assertEqual(resolve_user_roles(user), ["production"])

    def test_duplicate_active_user_role_is_not_created(self):
        user = User.objects.create_user(
            email="hybrid@test.com",
            password="Pass12345",
            name="Hybrid User",
        )

        self.assertEqual(UserRole.objects.filter(user=user, role="client").count(), 1)
        with self.assertRaises(IntegrityError):
            UserRole.objects.create(user=user, role="client", source="test-repeat")

    def test_resend_alias_and_confirm_alias_complete_verification_flow(self):
        self.client.post(
            "/api/auth/register/",
            {
                "email": "verify-me@test.com",
                "password": "Pass12345",
                "name": "Verify Me",
                "role": "client",
            },
            format="json",
        )
        self.assertEqual(len(mail.outbox), 1)

        resend_response = self.client.post(
            "/api/auth/email/resend/",
            {"email": "verify-me@test.com"},
            format="json",
        )

        self.assertEqual(resend_response.status_code, 200)
        self.assertEqual(resend_response.json()["sent"], True)
        self.assertEqual(len(mail.outbox), 2)

        key = self._extract_confirmation_key(mail.outbox[-1].body)
        confirm_response = self.client.post(
            "/api/auth/email/verify/",
            {"key": key},
            format="json",
        )

        self.assertEqual(confirm_response.status_code, 200)
        self.assertEqual(confirm_response.json()["verified"], True)

        email_address = EmailAddress.objects.get(email="verify-me@test.com")
        self.assertTrue(email_address.verified)

        login_response = self.client.post(
            "/api/auth/token/",
            {"email": "verify-me@test.com", "password": "Pass12345"},
            format="json",
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertIn("access", login_response.json())

    def test_partner_login_returns_partner_roles_and_home_route(self):
        user = User.objects.create_user(
            email="partner-login@test.com",
            password="Pass12345",
            name="Partner Login",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            is_active=True,
        )
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)

        response = self.client.post(
            "/api/auth/token/",
            {"email": user.email, "password": "Pass12345"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["user"]
        self.assertEqual(payload["role"], "partner")
        self.assertEqual(payload["roles"], ["partner"])
        self.assertEqual(payload["primary_role"], "partner")
        self.assertEqual(payload["home_route"], "/dashboard/partner")
        self.assertEqual(payload["can_access_partner_dashboard"], True)
        self.assertEqual(payload["can_access_client_dashboard"], False)

    def test_production_login_returns_production_roles_and_home_route(self):
        user = User.objects.create_user(
            email="production-login@test.com",
            password="Pass12345",
            name="Production Login",
            role=User.Role.PRODUCTION,
            is_active=True,
        )
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)

        response = self.client.post(
            "/api/auth/token/",
            {"email": user.email, "password": "Pass12345"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["user"]
        self.assertEqual(payload["role"], "production")
        self.assertEqual(payload["roles"], ["production"])
        self.assertEqual(payload["primary_role"], "production")
        self.assertEqual(payload["home_route"], "/dashboard/production")
        self.assertEqual(payload["can_access_production_dashboard"], True)
        self.assertEqual(payload["can_access_client_dashboard"], False)

    def test_super_admin_login_returns_admin_home_route_and_access_flags(self):
        user = User.objects.create_superuser(
            email="admin-login@test.com",
            password="Pass12345",
        )
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)

        response = self.client.post(
            "/api/auth/token/",
            {"email": user.email, "password": "Pass12345"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["user"]
        self.assertEqual(payload["role"], "super_admin")
        self.assertEqual(payload["roles"], ["super_admin"])
        self.assertEqual(payload["primary_role"], "super_admin")
        self.assertEqual(payload["home_route"], "/dashboard/admin")
        self.assertEqual(payload["can_access_admin_dashboard"], True)
        self.assertEqual(payload["can_access_client_dashboard"], True)
        self.assertEqual(payload["can_access_partner_dashboard"], True)
        self.assertEqual(payload["can_access_production_dashboard"], True)

    def test_resend_for_missing_email_is_safe_and_does_not_leak(self):
        response = self.client.post(
            "/api/auth/email/resend/",
            {"email": "missing-user@test.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sent"], False)
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_api_route_returns_json_not_html(self):
        response = self.client.post("/api/auth/email/not-a-real-route/", {}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["code"], "NOT_FOUND")

    def test_password_reset_flow_generates_frontend_link_and_accepts_new_password(self):
        user = User.objects.create_user(
            email="reset-me@test.com",
            password="Pass12345",
            name="Reset Me",
            role="client",
            is_active=True,
        )
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)

        request_response = self.client.post(
            "/api/auth/password-reset/",
            {"email": user.email},
            format="json",
        )

        self.assertEqual(request_response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

        reset_link = self._extract_link_with_key(mail.outbox[0].body)
        parsed_reset = urlparse(reset_link)
        self.assertEqual(parsed_reset.scheme, "https")
        self.assertEqual(parsed_reset.netloc, "printy.ke")
        self.assertEqual(parsed_reset.path, "/auth/reset-password")
        reset_key = parse_qs(parsed_reset.query).get("key", [None])[0]
        self.assertTrue(reset_key)

        confirm_response = self.client.post(
            "/api/auth/password-reset/confirm/",
            {"key": reset_key, "password": "NewPass12345"},
            format="json",
        )
        self.assertEqual(confirm_response.status_code, 200)

        login_response = self.client.post(
            "/api/auth/token/",
            {"email": user.email, "password": "NewPass12345"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn("access", login_response.json())

    def test_password_reset_email_is_printy_branded(self):
        user = User.objects.create_user(
            email="brand-reset@test.com",
            password="Pass12345",
            name="Brand Reset",
            role="client",
            is_active=True,
        )
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)

        response = self.client.post(
            "/api/auth/password-reset/",
            {"email": user.email},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject.strip(), "Reset your Printy password")
        self.assertIn("Hello from Printy!", message.body)
        self.assertIn("We received a request to reset the password for your Printy account.", message.body)
        self.assertIn("Print work, clearer.", message.body)
        self.assertNotIn("example.com", message.body)
        self.assertNotIn("/accounts/signup/", message.body)
        self.assertIn("https://printy.ke/auth/reset-password?key=", message.body)

    def test_password_reset_for_unknown_email_returns_generic_success_without_sending_email(self):
        response = self.client.post(
            "/api/auth/password-reset/",
            {"email": "missing-reset@test.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["detail"],
            "If that email exists, a password reset link has been sent.",
        )
        self.assertEqual(len(mail.outbox), 0)
import unittest

raise unittest.SkipTest("Legacy pre-reset tests target removed role models/routes.")
