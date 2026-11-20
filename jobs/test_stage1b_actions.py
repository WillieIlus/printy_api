"""Stage 1B: one perform-next-action endpoint and intake path."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from jobs.action_resolver import resolve_next_step
from jobs.card_projection import build_job_card
from jobs.choices import (
    JobAssignmentStatus,
    JobFileStatus,
    JobFileType,
    JobFileVisibility,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)
from jobs.models import JobAssignment, JobEvent, JobFile, ManagedJob
from jobs.timing_services import record_job_event
from jobs.printer_workflow import build_printer_progress
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class Stage1BActionBase(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.buyer = User.objects.create_user(
            email="1b-buyer@example.com", password="pass", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="1b-manager@example.com",
            password="pass",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(
            email="1b-printer@example.com", password="pass", role=User.Role.PRODUCTION
        )
        self.admin = User.objects.create_user(
            email="1b-admin@example.com",
            password="pass",
            role=User.Role.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        self.shop = Shop.objects.create(
            owner=self.printer, name="1B Press", slug="1b-press", is_active=True
        )
        self.request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="1B Buyer",
            status=QuoteStatus.SUBMITTED,
            request_snapshot={},
        )

    def make_job(self, *, status=ManagedJobStatus.ASSIGNED, broker=True, metadata=None, **kwargs):
        values = {
            "managed_reference": f"MJ-1B-{ManagedJob.objects.count() + 1}",
            "title": "1B job",
            "source_quote_request": self.request,
            "client": self.buyer,
            "created_by": self.buyer,
            "broker": self.manager if broker else None,
            "assigned_shop": self.shop,
            "status": status,
            "payment_status": ManagedJobPaymentStatus.CONFIRMED,
            "client_total": Decimal("1500.00"),
            "payment_confirmed_at": timezone.now(),
            "due_date": timezone.now() + timedelta(hours=80),
            "workflow_metadata": metadata or {
                "artwork_workflow": {"source": "manager_holds", "claim_state": "accepted"}
            },
        }
        values.update(kwargs)
        return ManagedJob.objects.create(**values)

    def make_assignment(self, job, *, status=JobAssignmentStatus.PENDING):
        return JobAssignment.objects.create(
            managed_job=job,
            assigned_shop=self.shop,
            status=status,
        )

    def complete_printer_stage(self, job, stage_key, action="completed", label=None):
        record_job_event(
            managed_job=job,
            stage="production",
            event=JobEvent.STAGE_COMPLETED if action == "completed" else JobEvent.STAGE_STARTED,
            actor=self.printer,
            actor_role="printer",
            source=JobEvent.SOURCE_PRINTER,
            metadata={
                "printer_stage": stage_key,
                "printer_stage_label": label or stage_key,
                "printer_action": action,
                "printer_action_label": label or stage_key,
            },
        )

    def make_qc_job(self):
        job = self.make_job(status=ManagedJobStatus.FINISHING)
        assignment = self.make_assignment(job, status=JobAssignmentStatus.FINISHING)
        self.complete_printer_stage(job, "artwork_check", label="Artwork check")
        self.complete_printer_stage(job, "printing", "started", "Printing")
        self.complete_printer_stage(job, "printing", "completed", "Printing")
        progress = build_printer_progress(assignment)
        self.assertEqual(progress["current_stage"], "quality_check", progress)
        return job, assignment

    def post_action(self, *, user, role, job, code, **extra):
        self.api.force_authenticate(user)
        return self.api.post(
            f"/api/managed-jobs/{job.id}/actions/next/",
            {"code": code, **extra},
            format="json",
            HTTP_X_PRINTY_ACTIVE_ROLE=role,
        )


class NextActionOwnershipTests(Stage1BActionBase):
    def test_wrong_code_is_refused_and_does_not_skip_a_stage(self):
        job = self.make_job()
        self.make_assignment(job, status=JobAssignmentStatus.PENDING)

        response = self.post_action(
            user=self.printer, role="production", job=job, code="pass_qc"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "wrong_action")
        self.assertEqual(
            JobAssignment.objects.get(managed_job=job).status,
            JobAssignmentStatus.PENDING,
        )

    def test_non_owner_admin_is_refused(self):
        job = self.make_job(status=ManagedJobStatus.PAYMENT_CONFIRMED)
        response = self.post_action(
            user=self.admin, role="super_admin", job=job, code="dispatch_job"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "not_owner")

    def test_buyer_cannot_accept_a_printer_job(self):
        job = self.make_job()
        self.make_assignment(job, status=JobAssignmentStatus.PENDING)
        response = self.post_action(
            user=self.buyer, role="client", job=job, code="accept_job"
        )
        self.assertIn(response.status_code, {403, 409})
        self.assertEqual(
            JobAssignment.objects.get(managed_job=job).status,
            JobAssignmentStatus.PENDING,
        )


class NextActionExecutionTests(Stage1BActionBase):
    def test_printer_accepts_and_the_thread_records_it(self):
        job = self.make_job()
        self.make_assignment(job, status=JobAssignmentStatus.PENDING)

        response = self.post_action(
            user=self.printer, role="production", job=job, code="accept_job"
        )

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["applied"])
        assignment = JobAssignment.objects.get(managed_job=job)
        self.assertEqual(assignment.status, JobAssignmentStatus.ACCEPTED)
        types = {row["type"] for row in body["card"]["jobs"][0]["thread"]}
        self.assertIn("stage", types)
        self.assertIn("note", types)

    def test_double_call_is_idempotent(self):
        job = self.make_job()
        assignment = self.make_assignment(job, status=JobAssignmentStatus.PENDING)

        first = self.post_action(
            user=self.printer, role="production", job=job, code="accept_job"
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertTrue(first.json()["applied"])

        second = self.post_action(
            user=self.printer, role="production", job=job, code="accept_job"
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertFalse(second.json()["applied"])
        self.assertTrue(second.json()["idempotent"])
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, JobAssignmentStatus.ACCEPTED)
        handoffs = JobEvent.objects.filter(
            managed_job=job,
            event=JobEvent.HANDOFF,
        ).count()
        self.assertEqual(handoffs, 1)

    def test_printer_qc_action(self):
        job, assignment = self.make_qc_job()
        response = self.post_action(
            user=self.printer, role="production", job=job, code="pass_qc"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["applied"])
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, JobAssignmentStatus.FINISHING)
        self.assertEqual(
            build_printer_progress(assignment)["next_action"]["stage_key"],
            "ready_for_dispatch",
        )

    def test_buyer_confirms_delivery(self):
        job = self.make_job(status=ManagedJobStatus.DELIVERED)
        self.make_assignment(job, status=JobAssignmentStatus.READY)

        response = self.post_action(
            user=self.buyer, role="client", job=job, code="confirm_delivery"
        )

        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.COMPLETED)


class IntakePathTests(Stage1BActionBase):
    def test_manager_sourced_job_has_manager_intake_path(self):
        job = self.make_job(status=ManagedJobStatus.PAYMENT_CONFIRMED)
        card = build_job_card(job, viewer_role="manager", user=self.manager)
        self.assertEqual(card["jobs"][0]["intake_path"], "manager")

    def test_direct_shop_job_has_direct_shop_intake_path(self):
        quote_request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Direct Buyer",
            status=QuoteStatus.SUBMITTED,
            request_snapshot={"direct_shop_intake": True},
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.buyer,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1500.00"),
            sent_at=timezone.now(),
        )
        job = self.make_job(
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            broker=False,
            source_quote_request=quote_request,
            source_quote=quote,
        )
        card = build_job_card(job, viewer_role="buyer", user=self.buyer)
        self.assertEqual(card["jobs"][0]["intake_path"], "direct_shop")
