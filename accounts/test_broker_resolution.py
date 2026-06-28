from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from accounts.services.broker_resolution import resolve_effective_broker
from accounts.services.system_accounts import HOUSE_BROKER_EMAIL, HOUSE_BROKER_NAME
from jobs.models import ManagedJob


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EffectiveBrokerResolutionTestCase(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="effective-broker-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.active_broker = self._broker("active-broker@test.com", active=True)
        self.inactive_broker = self._broker("inactive-broker@test.com", active=False)

    def _broker(self, email: str, *, active: bool) -> User:
        broker = User.objects.create_user(
            email=email,
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        UserProfile.objects.update_or_create(
            user=broker,
            defaults={"broker_profile_active": active},
        )
        return broker

    def _job(self, broker: User, *, seconds_ago: int) -> ManagedJob:
        job = ManagedJob.objects.create(
            client=self.client_user,
            broker=broker,
            created_by=broker,
            title=f"Brokered by {broker.email}",
        )
        job.created_at = timezone.now() - timezone.timedelta(seconds=seconds_ago)
        job.save(update_fields=["created_at", "updated_at"])
        return job

    def test_returns_none_when_client_has_no_prior_brokered_jobs(self):
        self.assertIsNone(resolve_effective_broker(self.client_user))

    def test_returns_most_recent_active_broker(self):
        self._job(self.active_broker, seconds_ago=60)

        self.assertEqual(resolve_effective_broker(self.client_user), self.active_broker.id)

    def test_returns_house_broker_when_most_recent_broker_is_inactive(self):
        self._job(self.inactive_broker, seconds_ago=60)

        broker_id = resolve_effective_broker(self.client_user)
        house_broker = User.objects.get(email=HOUSE_BROKER_EMAIL)
        self.assertEqual(broker_id, house_broker.id)
        self.assertEqual(house_broker.name, HOUSE_BROKER_NAME)
        self.assertEqual(house_broker.role, User.Role.PARTNER)
        self.assertTrue(house_broker.partner_profile_enabled)
        self.assertFalse(house_broker.is_staff)
        self.assertFalse(house_broker.is_superuser)
        self.assertTrue(house_broker.profile.broker_profile_active)

    def test_most_recent_brokered_job_wins_when_active_and_inactive_are_mixed(self):
        self._job(self.inactive_broker, seconds_ago=30)
        self._job(self.active_broker, seconds_ago=10)

        self.assertEqual(resolve_effective_broker(self.client_user), self.active_broker.id)

    def test_most_recent_inactive_broker_wins_over_older_active_broker(self):
        self._job(self.active_broker, seconds_ago=30)
        self._job(self.inactive_broker, seconds_ago=10)

        broker_id = resolve_effective_broker(self.client_user)
        self.assertEqual(broker_id, User.objects.get(email=HOUSE_BROKER_EMAIL).id)

    def test_management_command_creates_house_broker(self):
        call_command("create_printy_house_broker_user")

        house_broker = User.objects.get(email=HOUSE_BROKER_EMAIL)
        self.assertEqual(house_broker.name, HOUSE_BROKER_NAME)
        self.assertEqual(house_broker.role, User.Role.PARTNER)
        self.assertTrue(house_broker.partner_profile_enabled)
        self.assertFalse(house_broker.is_staff)
        self.assertFalse(house_broker.is_superuser)
        self.assertTrue(house_broker.profile.is_system_account)
        self.assertTrue(house_broker.profile.broker_profile_active)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BrokerProfileActiveToggleViewTestCase(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.admin_user = User.objects.create_superuser(email="broker-toggle-admin@test.com", password="pass12345")
        self.non_admin = User.objects.create_user(email="broker-toggle-user@test.com", password="pass12345")
        self.broker = User.objects.create_user(
            email="broker-toggle-broker@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        UserProfile.objects.create(user=self.broker)
        self.url = reverse("dashboard-admin-broker-active-toggle", kwargs={"user_id": self.broker.id})

    def test_superuser_can_toggle_broker_profile_active_flag(self):
        self.api_client.force_authenticate(user=self.admin_user)

        response = self.api_client.post(self.url, {"is_active": False}, format="json")

        self.assertEqual(response.status_code, 200)
        self.broker.profile.refresh_from_db()
        self.assertFalse(self.broker.profile.broker_profile_active)
        self.assertFalse(response.json()["broker_profile_active"])

    def test_non_superuser_cannot_toggle_broker_profile_active_flag(self):
        self.api_client.force_authenticate(user=self.non_admin)

        response = self.api_client.post(self.url, {"is_active": False}, format="json")

        self.assertEqual(response.status_code, 403)
