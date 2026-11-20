"""Stage 1D: admin verification, custody, disputes and reporting."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.admin.models import LogEntry
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from api.admin_audit import last_admin_audit
from inventory.models import Machine, Paper
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobExceptionStatus,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)
from jobs.models import JobAssignment, ManagedJob, ManagedJobPayout
from pricing.choices import ColorMode
from pricing.models import PrintingRate
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AdminOpsBase(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.admin = User.objects.create_user(
            email="1d-admin@example.com",
            password="pass",
            role=User.Role.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        self.buyer = User.objects.create_user(
            email="1d-buyer@example.com", password="pass", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="1d-manager@example.com",
            password="pass",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(
            email="1d-printer@example.com", password="pass", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(
            owner=self.printer, name="1D Press", slug="1d-press", is_active=True, is_public=True
        )
        self.manager_profile = UserProfile.objects.create(
            user=self.manager,
            manager_verified=False,
            broker_profile_active=True,
            manager_speciality="Business cards",
        )
        self.request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="1D Buyer",
            assigned_manager=self.manager,
            status=QuoteStatus.SUBMITTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            created_by=self.manager,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1500.00"),
            sent_at=timezone.now(),
        )
        self.job = ManagedJob.objects.create(
            managed_reference="MJ-1D-1",
            title="Completed job",
            source_quote_request=self.request,
            source_quote=self.quote,
            client=self.buyer,
            created_by=self.buyer,
            broker=self.manager,
            assigned_shop=self.shop,
            status=ManagedJobStatus.COMPLETED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=Decimal("1500.00"),
            broker_payout=Decimal("300.00"),
            printy_fee=Decimal("200.00"),
            payment_confirmed_at=timezone.now(),
            completed_at=timezone.now(),
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.job,
            assigned_shop=self.shop,
            status=JobAssignmentStatus.READY,
            shop_payout=Decimal("1000.00"),
        )

    def admin_get(self, path):
        self.api.force_authenticate(self.admin)
        return self.api.get(path)

    def admin_post(self, path, body):
        self.api.force_authenticate(self.admin)
        return self.api.post(path, body, format="json")

    def make_complete_shop(self):
        Paper.objects.create(
            shop=self.shop,
            name="300gsm Art Card",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            buying_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )
        machine = Machine.objects.filter(shop=self.shop, is_active=True).first()
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode=ColorMode.COLOR,
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
        )
        self.shop.standard_turnaround_hours = 48
        self.shop.save(update_fields=["standard_turnaround_hours"])


class AdminPermissionTests(AdminOpsBase):
    def test_non_admin_is_refused_on_every_admin_surface(self):
        paths = [
            "/api/admin/verifications/managers/",
            "/api/admin/verifications/printers/",
            "/api/admin/custody/",
            "/api/admin/disputes/",
            "/api/admin/reporting/",
        ]
        for user in (self.buyer, self.manager, self.printer):
            self.api.force_authenticate(user)
            for path in paths:
                with self.subTest(user=user.email, path=path):
                    self.assertIn(self.api.get(path).status_code, {401, 403})

    def test_non_admin_cannot_approve_or_release(self):
        self.api.force_authenticate(self.manager)
        self.assertIn(
            self.api.post(
                f"/api/admin/verifications/managers/{self.manager.id}/decision/",
                {"decision": "approve"},
                format="json",
            ).status_code,
            {401, 403},
        )
        self.api.force_authenticate(self.buyer)
        self.assertIn(
            self.api.post(
                f"/api/admin/custody/{self.job.id}/release/",
                {},
                format="json",
            ).status_code,
            {401, 403},
        )


class ManagerVerificationTests(AdminOpsBase):
    def test_pending_list_and_approve(self):
        response = self.admin_get("/api/admin/verifications/managers/?status=pending")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["status"], "pending")

        decision = self.admin_post(
            f"/api/admin/verifications/managers/{self.manager.id}/decision/",
            {"decision": "approve"},
        )
        self.assertEqual(decision.status_code, 200, decision.content)
        self.manager_profile.refresh_from_db()
        self.assertTrue(self.manager_profile.manager_verified)
        self.assertIsNotNone(self.manager_profile.manager_verified_at)
        self.assertTrue(self.manager_profile.broker_profile_active)
        self.assertEqual(last_admin_audit(obj=self.manager_profile)["action"], "manager_approve")

    def test_reject_requires_reason_and_records_it(self):
        no_reason = self.admin_post(
            f"/api/admin/verifications/managers/{self.manager.id}/decision/",
            {"decision": "reject"},
        )
        self.assertEqual(no_reason.status_code, 400)
        self.assertEqual(no_reason.json()["code"], "reason_required")

        decision = self.admin_post(
            f"/api/admin/verifications/managers/{self.manager.id}/decision/",
            {"decision": "reject", "reason": "Incomplete profile"},
        )
        self.assertEqual(decision.status_code, 200, decision.content)
        self.manager_profile.refresh_from_db()
        self.assertFalse(self.manager_profile.manager_verified)
        self.assertFalse(self.manager_profile.broker_profile_active)
        audit = last_admin_audit(obj=self.manager_profile)
        self.assertEqual(audit["action"], "manager_reject")
        self.assertEqual(audit["reason"], "Incomplete profile")


class PrinterVerificationTests(AdminOpsBase):
    def test_pending_list_and_approve_completes_setup(self):
        self.make_complete_shop()
        pending = self.admin_get("/api/admin/verifications/printers/?status=pending")
        self.assertEqual(pending.status_code, 200, pending.content)
        self.assertTrue(any(row["id"] == self.shop.id for row in pending.json()["results"]))

        decision = self.admin_post(
            f"/api/admin/verifications/printers/{self.shop.id}/decision/",
            {"decision": "approve"},
        )
        self.assertEqual(decision.status_code, 200, decision.content)
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_active)
        self.assertTrue(self.shop.is_public)
        self.assertEqual(decision.json()["status"], "verified")
        self.assertEqual(last_admin_audit(obj=self.shop)["action"], "printer_approve")

    def test_reject_requires_reason_and_deactivates(self):
        decision = self.admin_post(
            f"/api/admin/verifications/printers/{self.shop.id}/decision/",
            {"decision": "reject", "reason": "Rate card incomplete"},
        )
        self.assertEqual(decision.status_code, 200, decision.content)
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_active)
        self.assertFalse(self.shop.is_public)
        audit = last_admin_audit(obj=self.shop)
        self.assertEqual(audit["action"], "printer_reject")
        self.assertEqual(audit["reason"], "Rate card incomplete")


class CustodyTests(AdminOpsBase):
    def test_list_and_release_completed_custody(self):
        listing = self.admin_get("/api/admin/custody/")
        self.assertEqual(listing.status_code, 200, listing.content)
        row = next(item for item in listing.json()["results"] if item["job_id"] == self.job.id)
        self.assertEqual(row["custody_status"], "held")
        self.assertTrue(row["can_release"])

        release = self.admin_post(f"/api/admin/custody/{self.job.id}/release/", {})
        self.assertEqual(release.status_code, 200, release.content)
        self.job.refresh_from_db()
        self.assertEqual(self.job.payment_status, ManagedJobPaymentStatus.RELEASED)
        self.assertEqual(ManagedJobPayout.objects.filter(managed_job=self.job).count(), 2)
        self.assertEqual(last_admin_audit(obj=self.job)["action"], "custody_release")

    def test_open_job_cannot_release_custody(self):
        self.job.status = ManagedJobStatus.READY
        self.job.save(update_fields=["status", "updated_at"])
        response = self.admin_post(f"/api/admin/custody/{self.job.id}/release/", {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "not_completed")


class DisputeTests(AdminOpsBase):
    def _disputed_job(self):
        self.job.status = ManagedJobStatus.DISPUTED
        self.job.dispute_open = True
        self.job.exception_status = ManagedJobExceptionStatus.DISPUTE_OPEN
        self.job.disputed_at = timezone.now()
        self.job.save(update_fields=["status", "dispute_open", "exception_status", "disputed_at", "updated_at"])
        return self.job

    def test_list_and_dismiss_dispute_with_audit(self):
        job = self._disputed_job()
        listing = self.admin_get("/api/admin/disputes/")
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertTrue(any(row["job_id"] == job.id for row in listing.json()["results"]))

        response = self.admin_post(
            f"/api/admin/disputes/{job.id}/resolve/",
            {"resolution": "dismiss", "reason": "Records agree"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertFalse(job.dispute_open)
        self.assertEqual(job.exception_status, ManagedJobExceptionStatus.CLEAR)
        self.assertEqual(job.status, ManagedJobStatus.READY)
        self.assertEqual(last_admin_audit(obj=job)["action"], "dispute_dismiss")

    def test_release_dispute_releases_payouts(self):
        job = self._disputed_job()
        job.delivered_at = timezone.now()
        job.save(update_fields=["delivered_at", "updated_at"])
        response = self.admin_post(
            f"/api/admin/disputes/{job.id}/resolve/",
            {"resolution": "release", "reason": "Work delivered"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertFalse(job.dispute_open)
        self.assertEqual(job.status, ManagedJobStatus.COMPLETED)
        self.assertEqual(job.payment_status, ManagedJobPaymentStatus.RELEASED)
        self.assertEqual(ManagedJobPayout.objects.filter(managed_job=job).count(), 2)

    def test_refund_dispute_uses_existing_refunded_status(self):
        job = self._disputed_job()
        response = self.admin_post(
            f"/api/admin/disputes/{job.id}/resolve/",
            {"resolution": "refund", "reason": "Print not delivered"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.CANCELLED)
        self.assertEqual(job.payment_status, ManagedJobPaymentStatus.REFUNDED)


class ReportingTests(AdminOpsBase):
    def test_reporting_uses_real_data(self):
        self.make_complete_shop()
        response = self.admin_get("/api/admin/reporting/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertGreaterEqual(body["managers"]["pending"], 1)
        self.assertGreaterEqual(body["printers"]["pending"], 1)
        self.assertGreaterEqual(body["jobs"]["total"], 1)
        self.assertIn("custody", body)
        self.assertIn("payouts", body)
        self.assertIn("payments", body)
