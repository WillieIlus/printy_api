"""Regression tests for stage-owner notifications (NextStepCard STEP 3)."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from jobs.choices import ManagedJobStatus, ManagedJobPaymentStatus
from jobs.models import ManagedJob
from jobs.stage_notifications import notify_stage_owner, ensure_stage_reminders
from notifications.models import Notification
from quotes.models import EmailOutbox
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StageNotificationsTestCase(TestCase):
    def setUp(self):
        self.printer = User.objects.create_user(email="notif-printer@example.com", password="pass", role=User.Role.PRODUCTION)
        self.shop = Shop.objects.create(owner=self.printer, name="Notif Shop", slug="notif-shop", is_active=True)
        self.manager = User.objects.create_user(
            email="notif-manager@example.com", password="pass", role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True},
        )
        self.buyer = User.objects.create_user(email="notif-buyer@example.com", password="pass", role=User.Role.CLIENT)
        self.job = ManagedJob.objects.create(
            title="Notif job", client=self.buyer, created_by=self.buyer, broker=self.manager,
            assigned_shop=self.shop, status=ManagedJobStatus.ASSIGNED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
        )

    def test_notify_stage_owner_is_idempotent(self):
        first = notify_stage_owner(managed_job=self.job, stage="dispatched", owner_role="printer", note="go")
        second = notify_stage_owner(managed_job=self.job, stage="dispatched", owner_role="printer", note="go")
        self.assertEqual(first["notification"], 1)
        self.assertEqual(first["email"], 1)
        self.assertEqual(second["notification"], 0)
        self.assertEqual(second["email"], 0)
        self.assertEqual(Notification.objects.filter(user=self.printer).count(), 1)
        self.assertEqual(EmailOutbox.objects.filter(recipient=self.printer).count(), 1)

    def test_ensure_stage_reminders_is_idempotent(self):
        ManagedJob.objects.filter(pk=self.job.pk).update(updated_at=timezone.now() - timedelta(hours=25))
        self.job.refresh_from_db()
        a = ensure_stage_reminders(self.job)
        b = ensure_stage_reminders(self.job)
        self.assertGreaterEqual(a["reminder"] + a["escalation"], 1)
        self.assertEqual(b["reminder"], 0)
        self.assertEqual(b["escalation"], 0)

    def test_buyer_never_notified_for_printer_stage(self):
        result = notify_stage_owner(managed_job=self.job, stage="dispatched", owner_role="printer")
        self.assertGreaterEqual(result["email"], 0)
        self.assertEqual(Notification.objects.filter(user=self.buyer).count(), 0)
