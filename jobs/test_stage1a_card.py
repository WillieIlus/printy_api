"""Stage 1A: QC ownership, the one card envelope, and role privacy."""

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
from jobs.messaging import create_job_message
from jobs.printer_workflow import build_printer_progress
from jobs.stage_mapping import (
    CANONICAL_STAGE_KEYS,
    STAGE_LABELS,
    STATUS_TO_STAGE,
    canonical_stage_for_job,
    canonical_stage_for_status,
)
from jobs.timing_config import OWNER_PRINTER, STAGES_BY_KEY, stage_for_status
from jobs.timing import stage_owner
from jobs.timing_services import record_job_event
from jobs.audit_services import record_job_status_event
from payments.models import Payment
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class Stage1ABase(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.buyer = User.objects.create_user(
            email="card-buyer@example.com", password="pass", role=User.Role.CLIENT
        )
        self.other_buyer = User.objects.create_user(
            email="card-other@example.com", password="pass", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="card-manager@example.com",
            password="pass",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(
            email="card-printer@example.com", password="pass", role=User.Role.PRODUCTION
        )
        self.admin = User.objects.create_user(
            email="card-admin@example.com",
            password="pass",
            role=User.Role.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        self.shop = Shop.objects.create(
            owner=self.printer, name="Card Test Press", slug="card-test-press", is_active=True
        )

    def make_job(
        self,
        *,
        status=ManagedJobStatus.PAYMENT_CONFIRMED,
        payment_status=ManagedJobPaymentStatus.CONFIRMED,
        **kwargs,
    ):
        request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Card Buyer",
            status=QuoteStatus.SUBMITTED,
            request_snapshot={},
        )
        values = {
            "title": "Business cards",
            "source_quote_request": request,
            "client": self.buyer,
            "created_by": self.buyer,
            "broker": self.manager,
            "assigned_shop": self.shop,
            "status": status,
            "payment_status": payment_status,
            "client_total": Decimal("1500.00"),
            "payment_confirmed_at": timezone.now(),
            "due_date": timezone.now() + timedelta(hours=80),
        }
        values.update(kwargs)
        return ManagedJob.objects.create(**values)

    def make_assignment(self, job, *, status=JobAssignmentStatus.ACCEPTED):
        return JobAssignment.objects.create(
            managed_job=job,
            assigned_shop=self.shop,
            status=status,
            accepted_at=timezone.now(),
        )

    def complete_printer_stage(self, job, assignment, stage_key, action="completed", label=None):
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
        self.complete_printer_stage(job, assignment, "artwork_check", label="Artwork check")
        self.complete_printer_stage(job, assignment, "printing", "started", "Printing")
        self.complete_printer_stage(job, assignment, "printing", "completed", "Printing")
        progress = build_printer_progress(assignment)
        self.assertEqual(progress["current_stage"], "quality_check", progress)
        return job, assignment

    def get_card(self, *, user, role, job):
        self.api.force_authenticate(user)
        return self.api.get(
            f"/api/managed-jobs/{job.id}/card/",
            HTTP_X_PRINTY_ACTIVE_ROLE=role,
        )


class StageMappingTests(Stage1ABase):
    def test_every_stored_managed_job_status_is_mapped(self):
        self.assertEqual({str(key) for key in STATUS_TO_STAGE}, set(ManagedJobStatus.values))
        for value in ManagedJobStatus.values:
            with self.subTest(status=value):
                self.assertIn(canonical_stage_for_status(value), CANONICAL_STAGE_KEYS)
                self.assertIn(stage_for_status(value), CANONICAL_STAGE_KEYS)
        self.assertEqual(set(STAGE_LABELS), set(CANONICAL_STAGE_KEYS))

    def test_qc_owner_is_the_printer(self):
        self.assertEqual(STAGES_BY_KEY["qc"].owner, OWNER_PRINTER)
        self.assertEqual(stage_owner("qc"), "printer")


class QCOwnershipTests(Stage1ABase):
    def test_printer_gets_the_qc_action(self):
        job, _assignment = self.make_qc_job()
        step = resolve_next_step(job, "printer")

        self.assertEqual(step["canonical_stage"], "qc")
        self.assertEqual(step["owner_role"], "printer")
        self.assertTrue(step["is_mine"])
        self.assertEqual(step["next_action"]["key"], "pass_qc")
        self.assertEqual(step["next_action"]["label"], "QC passed")
        self.assertEqual(step["next_action"]["actor"], "printer")
        self.assertEqual(step["next_action"]["params"]["printer_stage"], "quality_check")

    def test_manager_and_buyer_see_read_only_qc_waiting(self):
        job, _assignment = self.make_qc_job()

        manager_step = resolve_next_step(job, "manager")
        self.assertEqual(manager_step["canonical_stage"], "qc")
        self.assertEqual(manager_step["owner_role"], "printer")
        self.assertFalse(manager_step["is_mine"])
        self.assertEqual(manager_step["next_action"]["key"], "none")
        self.assertEqual(manager_step["next_action"]["label"], "Waiting on the printer")

        buyer_step = resolve_next_step(job, "buyer")
        self.assertEqual(buyer_step["canonical_stage"], "qc")
        # The buyer's contract is with the manager; the shop is never exposed.
        self.assertEqual(buyer_step["owner_role"], "manager")
        self.assertFalse(buyer_step["is_mine"])
        self.assertEqual(buyer_step["next_action"]["key"], "none")
        self.assertNotIn("printer", buyer_step["next_action"]["label"].lower())

    def test_manager_never_gets_a_manager_confirm_qc_action(self):
        job, _assignment = self.make_qc_job()
        step = resolve_next_step(job, "manager")
        self.assertNotEqual(step["next_action"]["key"], "pass_qc")


class RoleStageMatrixTests(Stage1ABase):
    def _case(self, stage_key, status, assignment_status=None, printing_started=False):
        job = self.make_job(status=status)
        if assignment_status is not None:
            self.make_assignment(job, status=assignment_status)
        if printing_started:
            assignment = job.assignments.first()
            self.complete_printer_stage(job, assignment, "printing", "started", "Printing")
        self.assertEqual(canonical_stage_for_job(job), stage_key)
        return job

    def test_owner_and_next_action_matrix(self):
        cases = [
            ("quote_accepted", ManagedJobStatus.DRAFT, None, False, {
                "buyer": "accept_quote", "manager": "none", "printer": "none",
            }),
            ("artwork_approval", ManagedJobStatus.PAYMENT_CONFIRMED, None, False, {
                "buyer": "upload_artwork", "manager": "none", "printer": "none",
            }),
            ("production", ManagedJobStatus.IN_PRODUCTION, JobAssignmentStatus.IN_PRODUCTION, True, {
                "buyer": "none", "manager": "none", "printer": "advance_production",
            }),
            ("delivery", ManagedJobStatus.READY, JobAssignmentStatus.READY, False, {
                "buyer": "none", "manager": "coordinate_delivery", "printer": "none",
            }),
        ]
        for stage_key, status, assignment_status, printing_started, expected in cases:
            with self.subTest(stage=stage_key):
                job = self._case(stage_key, status, assignment_status, printing_started)
                for role in ("buyer", "manager", "printer"):
                    step = resolve_next_step(job, role)
                    self.assertEqual(step["canonical_stage"], stage_key)
                    self.assertEqual(step["next_action"]["key"], expected[role])

        qc_job, _assignment = self.make_qc_job()
        qc_expected = {"buyer": "none", "manager": "none", "printer": "pass_qc"}
        self.assertEqual(canonical_stage_for_job(qc_job), "qc")
        for role, code in qc_expected.items():
            step = resolve_next_step(qc_job, role)
            self.assertEqual(step["canonical_stage"], "qc")
            self.assertEqual(step["next_action"]["key"], code)


class CardEndpointTests(Stage1ABase):
    def _decorate_job(self):
        job = self.make_job(status=ManagedJobStatus.PAYMENT_CONFIRMED)
        record_job_event(
            managed_job=job,
            stage="quote_accepted",
            event=JobEvent.STAGE_STARTED,
            actor=self.manager,
            actor_role="manager",
            source=JobEvent.SOURCE_MANAGER,
            metadata={"reason": "quote_accepted"},
        )
        Payment.objects.create(
            managed_job=job,
            amount=Decimal("987654.00"),
            currency="KES",
            status=Payment.STATUS_PAID,
            confirmed_at=timezone.now(),
        )
        JobFile.objects.create(
            managed_job=job,
            file_type=JobFileType.ARTWORK,
            visibility=JobFileVisibility.CLIENT,
            status=JobFileStatus.UPLOADED,
            original_filename="secret-client-name.pdf",
            uploaded_by=self.buyer,
        )
        create_job_message(
            job=job,
            user=self.buyer,
            body="Please print these cards.",
            stage_key="quote_accepted",
        )
        record_job_status_event(
            managed_job=job,
            event_type="job_delivered",
            actor=self.manager,
            summary="Job delivered.",
        )
        return job

    def test_card_envelope_shape_and_one_job_list(self):
        job = self.make_job()
        response = self.get_card(user=self.buyer, role="client", job=job)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["id"], f"job-card-{job.id}")
        self.assertEqual(body["viewer"]["role"], "buyer")
        self.assertEqual(len(body["jobs"]), 1)
        item = body["jobs"][0]
        self.assertIn(item["stage"]["key"], CANONICAL_STAGE_KEYS)
        self.assertEqual(set(item["next_action"]), {"code", "label", "actor", "params"})
        self.assertIn("due", item)
        self.assertIsInstance(item["thread"], list)
        self.assertIsInstance(item["secondary_actions"], list)
        self.assertNotIn("hold", [a["code"] for a in item["secondary_actions"]])

    def test_buyer_card_hides_shop_and_internal_status_but_sees_manager(self):
        job = self._decorate_job()
        response = self.get_card(user=self.buyer, role="client", job=job)
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()["jobs"][0]
        self.assertNotIn("shop", item)
        self.assertNotIn("client", item)
        self.assertNotIn("internal_status", item)
        self.assertEqual(item["manager"]["name"], self.manager.name or "Manager")
        raw = response.content.decode()
        self.assertNotIn(self.shop.name, raw)
        self.assertNotIn(str(ManagedJobStatus.PAYMENT_CONFIRMED), raw)

    def test_manager_card_sees_shop_and_client(self):
        job = self._decorate_job()
        response = self.get_card(user=self.manager, role="partner", job=job)
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()["jobs"][0]
        self.assertEqual(item["shop"]["name"], self.shop.name)
        self.assertIn("client", item)
        self.assertNotIn("internal_status", item)

    def test_printer_card_hides_client_identity_and_total(self):
        job = self.make_job(status=ManagedJobStatus.IN_PRODUCTION)
        self.make_assignment(job, status=JobAssignmentStatus.IN_PRODUCTION)
        Payment.objects.create(
            managed_job=job,
            amount=Decimal("987654.00"),
            currency="KES",
            status=Payment.STATUS_PAID,
            confirmed_at=timezone.now(),
        )
        JobFile.objects.create(
            managed_job=job,
            file_type=JobFileType.ARTWORK,
            visibility=JobFileVisibility.CLIENT,
            status=JobFileStatus.UPLOADED,
            original_filename="secret-client-name.pdf",
            uploaded_by=self.buyer,
        )
        response = self.get_card(user=self.printer, role="production", job=job)
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()["jobs"][0]
        self.assertNotIn("client", item)
        self.assertNotIn("shop", item)
        self.assertNotIn("internal_status", item)
        raw = response.content.decode()
        self.assertNotIn(self.buyer.name or "Card Buyer", raw)
        self.assertNotIn("987654", raw)
        self.assertNotIn("secret-client-name.pdf", raw)
        payment_items = [row for row in item["thread"] if row["type"] == "payment"]
        self.assertTrue(payment_items)
        self.assertNotIn("amount", payment_items[0]["metadata"])

    def test_admin_card_can_see_internal_status_and_shop(self):
        job = self._decorate_job()
        response = self.get_card(user=self.admin, role="super_admin", job=job)
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()["jobs"][0]
        self.assertEqual(item["internal_status"], ManagedJobStatus.PAYMENT_CONFIRMED)
        self.assertEqual(item["shop"]["name"], self.shop.name)
        self.assertIn("client", item)

    def test_card_rejects_a_role_the_account_does_not_hold(self):
        job = self.make_job()
        response = self.get_card(user=self.buyer, role="production", job=job)
        self.assertEqual(response.status_code, 403)

    def test_card_rejects_an_unrelated_user(self):
        job = self.make_job()
        response = self.get_card(user=self.other_buyer, role="client", job=job)
        self.assertEqual(response.status_code, 403)

    def test_thread_is_one_chronological_list(self):
        job = self._decorate_job()
        response = self.get_card(user=self.buyer, role="client", job=job)
        thread = response.json()["jobs"][0]["thread"]
        types = {row["type"] for row in thread}
        self.assertTrue({"stage", "message", "artwork", "payment", "note"} <= types)
        timestamps = [row["at"] for row in thread if row["at"]]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertTrue(all(row["stage"] in CANONICAL_STAGE_KEYS for row in thread if row["stage"]))
