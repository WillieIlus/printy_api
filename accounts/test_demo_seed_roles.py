from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import User
from accounts.services.roles import resolve_user_roles


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DemoSeedRolesTestCase(TestCase):
    """The demo manager/shop must land on their own dashboards, not admin."""

    def test_demo_manager_and_shop_are_not_platform_admins(self):
        call_command("seed_mvp_data", verbosity=0)

        manager = User.objects.get(email="manager@printy.local")
        shop = User.objects.get(email="shop@printy.local")
        admin = User.objects.get(email="admin@printy.local")

        self.assertFalse(manager.is_staff)
        self.assertFalse(manager.is_superuser)
        self.assertEqual(resolve_user_roles(manager), ["partner"])

        self.assertFalse(shop.is_staff)
        self.assertFalse(shop.is_superuser)
        self.assertEqual(resolve_user_roles(shop), ["production"])

        self.assertTrue(admin.is_staff)
        self.assertEqual(resolve_user_roles(admin), ["super_admin"])

    def test_reseeding_repairs_a_manager_marked_as_platform_staff(self):
        User.objects.create_user(
            email="manager@printy.local",
            password="PrintyReset123!",
            name="Printy Manager",
            role=User.Role.BROKER,
            is_staff=True,
            is_superuser=True,
            partner_profile_enabled=True,
        )

        call_command("seed_mvp_data", verbosity=0)

        manager = User.objects.get(email="manager@printy.local")
        self.assertFalse(manager.is_staff)
        self.assertFalse(manager.is_superuser)
        self.assertEqual(resolve_user_roles(manager), ["partner"])
