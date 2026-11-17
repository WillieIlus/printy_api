"""Regression tests for the NextStepCard single-resolver (STEP 2/4)."""

from decimal import Decimal
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from jobs.choices import ManagedJobStatus, ManagedJobPaymentStatus
from jobs.models import ManagedJob
from jobs.action_resolver import resolve_next_step, BUYER, MANAGER, PRINTER, NONE
from quotes.models import QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NextStepResolverTestCase(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.buyer = User.objects.create_user(email="ns-buyer@example.com", password="pass", role=User.Role.CLIENT)
        self.manager = User.objects.create_user(
            email="ns-manager@example.com", password="pass", role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(email="ns-printer@example.com", password="pass", role=User.Role.PRODUCTION)
        self.shop = Shop.objects.create(owner=self.printer, name="NS Shop", slug="ns-shop", is_active=True)
        self.job = ManagedJob.objects.create(
            title="NS job", client=self.buyer, created_by=self.buyer, broker=self.manager,
            assigned_shop=self.shop, status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED, client_total=Decimal("1500.00"),
            payment_confirmed_at=timezone.now(),
            due_date=timezone.now() + timedelta(hours=80),
        )

    def test_resolver_shape_and_buyer_ownership_for_artwork_step(self):
        step = resolve_next_step(self.job, "buyer")
        self.assertEqual(step["owner_role"], "buyer")
        self.assertEqual(step["current_stage"], ManagedJobStatus.PAYMENT_CONFIRMED)
        self.assertEqual(step["next_action"]["key"], "upload_artwork")
        self.assertIsNotNone(step["next_action"]["target"])
        self.assertIn("is_mine", step)
        self.assertEqual(step["overdue_level"], "none")

    def test_resolver_rejects_printer_ownership_for_buyer(self):
        self.job.status = ManagedJobStatus.IN_PRODUCTION
        step = resolve_next_step(self.job, "buyer")
        self.assertEqual(step["owner_role"], "manager")
        self.assertFalse(step["is_mine"])
        self.assertEqual(step["next_action"]["key"], "monitor_production")

    def test_resolver_marks_printer_mine_for_production_stage(self):
        self.job.status = ManagedJobStatus.IN_PRODUCTION
        step = resolve_next_step(self.job, "printer")
        self.assertEqual(step["owner_role"], "printer")
        self.assertTrue(step["is_mine"])
        self.assertEqual(step["next_action"]["key"], "advance_production")
        self.assertIn("/app/printer", step["next_action"]["target"])

    def test_client_job_payload_carries_next_step(self):
        self.api.force_authenticate(self.buyer)
        response = self.api.get(f"/api/dashboard/client/jobs/{self.job.id}/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()["job"]
        self.assertIn("next_step", payload)
        self.assertEqual(payload["next_step"]["owner_role"], "buyer")
        self.assertEqual(payload["next_step"]["next_action"]["key"], "upload_artwork")

    def test_needs_my_action_endpoint_returns_buyer_job(self):
        self.api.force_authenticate(self.buyer)
        response = self.api.get("/api/dashboard/needs-my-action/", HTTP_X_PRINTY_ACTIVE_ROLE="client")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreaterEqual(body["count"], 1)
        self.assertTrue(any(item["job_id"] == self.job.id for item in body["results"]))
        self.assertEqual(body["role"], "buyer")

    def test_needs_my_action_endpoint_role_guard(self):
        self.api.force_authenticate(self.buyer)
        response = self.api.get("/api/dashboard/needs-my-action/", HTTP_X_PRINTY_ACTIVE_ROLE="production")
        self.assertEqual(response.status_code, 403)

    def test_tracked_job_payload_carries_next_step(self):
        self.api.force_authenticate(self.buyer)
        response = self.api.get(f"/api/dashboard/client/tracked-jobs/{self.job.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("next_step", response.json()["job"])
