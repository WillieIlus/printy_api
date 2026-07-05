from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from jobs.audit_services import EVENT_PAYOUT_RELEASED
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, JobStatusEvent, ManagedJob, ManagedJobPayout
from payments.models import Payment
from shops.models import Shop


User = get_user_model()


class ManualPayoutReleaseTestCase(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            email="phase17-admin@example.com",
            password="pass12345",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            email="phase17-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="phase17-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
        )
        self.shop_owner = User.objects.create_user(
            email="phase17-shop@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(name="Phase 17 Shop", owner=self.shop_owner, is_active=True)
        self.managed_job = ManagedJob.objects.create(
            title="Phase 17 Job",
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            status=ManagedJobStatus.READY,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=Decimal("1500.00"),
            broker_payout=Decimal("300.00"),
            ready_at=timezone.now(),
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.managed_job,
            assigned_shop=self.shop,
            status="ready",
            shop_payout=Decimal("1000.00"),
        )
        self.payment = Payment.objects.create(
            managed_job=self.managed_job,
            payer=self.client_user,
            amount=Decimal("1500.00"),
            expected_amount=Decimal("1500.00"),
            received_amount=Decimal("1500.00"),
            method=Payment.METHOD_MANUAL,
            provider="manual",
            status=Payment.STATUS_PAID,
            confirmed_at=timezone.now(),
        )
        self.client = APIClient()

    def _release(self, user=None):
        self.client.force_authenticate(user=user or self.admin_user)
        return self.client.post(reverse("managed-job-payout-release", kwargs={"pk": self.managed_job.id}), {})

    def test_cannot_release_before_ready_or_completion(self):
        self.managed_job.status = ManagedJobStatus.IN_PRODUCTION
        self.managed_job.ready_at = None
        self.managed_job.save(update_fields=["status", "ready_at", "updated_at"])

        response = self._release()

        self.assertEqual(response.status_code, 400)
        self.assertIn("ready or completed", response.json()["detail"])
        self.assertEqual(ManagedJobPayout.objects.count(), 0)
        self.assertEqual(JobStatusEvent.objects.filter(event_type=EVENT_PAYOUT_RELEASED).count(), 0)

    def test_authorized_admin_releases_payouts_and_audit_once(self):
        response = self._release()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["idempotent"])
        self.assertEqual(payload["payout_status"], "paid")
        self.assertEqual(ManagedJobPayout.objects.count(), 2)
        manager_payout = ManagedJobPayout.objects.get(recipient_role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER)
        shop_payout = ManagedJobPayout.objects.get(recipient_role=ManagedJobPayout.RECIPIENT_ROLE_SHOP)
        self.assertEqual(manager_payout.status, ManagedJobPayout.STATUS_RELEASED)
        self.assertEqual(manager_payout.amount, Decimal("300.00"))
        self.assertEqual(manager_payout.recipient, self.manager)
        self.assertEqual(shop_payout.status, ManagedJobPayout.STATUS_RELEASED)
        self.assertEqual(shop_payout.amount, Decimal("1000.00"))
        self.assertEqual(shop_payout.recipient, self.shop_owner)
        self.managed_job.refresh_from_db()
        self.assertEqual(self.managed_job.payment_status, ManagedJobPaymentStatus.RELEASED)
        event = JobStatusEvent.objects.get(event_type=EVENT_PAYOUT_RELEASED)
        self.assertEqual(event.actor, self.admin_user)
        self.assertEqual(set(event.metadata["recipient_roles"]), {"manager", "shop"})

    def test_duplicate_release_is_idempotent_without_duplicate_records_or_audit(self):
        first = self._release()
        second = self._release()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["idempotent"])
        self.assertEqual(ManagedJobPayout.objects.count(), 2)
        self.assertEqual(JobStatusEvent.objects.filter(event_type=EVENT_PAYOUT_RELEASED).count(), 1)

    def test_wrong_role_blocked(self):
        response = self._release(user=self.manager)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ManagedJobPayout.objects.count(), 0)

    def test_settlement_status_is_role_safe_after_release(self):
        self._release()

        self.client.force_authenticate(user=self.manager)
        manager_response = self.client.get(reverse("managed-job-settlement", kwargs={"pk": self.managed_job.id}))
        self.assertEqual(manager_response.status_code, 200)
        manager_payload = manager_response.json()
        self.assertEqual(manager_payload["payout_status"], "paid")
        self.assertEqual(manager_payload["expected_manager_payout"], "300.00")
        self.assertNotIn("expected_production_payout", manager_payload)

        self.client.force_authenticate(user=self.shop_owner)
        shop_response = self.client.get(reverse("managed-job-settlement", kwargs={"pk": self.managed_job.id}))
        self.assertEqual(shop_response.status_code, 200)
        shop_payload = shop_response.json()
        self.assertEqual(shop_payload["payout_status"], "paid")
        self.assertEqual(shop_payload["expected_production_payout"], "1000.00")
        self.assertNotIn("expected_manager_payout", shop_payload)

        self.client.force_authenticate(user=self.client_user)
        client_response = self.client.get(reverse("managed-job-settlement", kwargs={"pk": self.managed_job.id}))
        self.assertEqual(client_response.status_code, 200)
        client_payload = client_response.json()
        self.assertEqual(client_payload["status"], "not_available")
        self.assertNotIn("expected_manager_payout", client_payload)
        self.assertNotIn("expected_production_payout", client_payload)
