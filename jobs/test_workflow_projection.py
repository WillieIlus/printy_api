"""Tests for the seven-stage workflow projection (read-only)."""

from __future__ import annotations

from django.test import override_settings

from jobs.card_projection import build_job_card
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.test_stage1a_card import Stage1ABase
from jobs.workflow_projection import (
    WORKFLOW_STAGE_INDEX,
    WORKFLOW_STAGE_KEYS,
    WORKFLOW_STAGE_LABELS,
    build_workflow,
)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WorkflowProjectionTests(Stage1ABase):
    def workflow(self, job, role="buyer"):
        return build_workflow(job, viewer_role=role)

    def current(self, job, role="buyer"):
        return self.workflow(job, role)["current"]

    def test_every_managed_job_status_has_one_seven_stage_projection(self):
        for value in ManagedJobStatus.values:
            with self.subTest(status=value):
                job = self.make_job(
                    status=value,
                    payment_status=ManagedJobPaymentStatus.PENDING,
                )
                payload = self.workflow(job)
                self.assertEqual(len(payload["stages"]), 7)
                self.assertEqual(
                    [stage["key"] for stage in payload["stages"]],
                    list(WORKFLOW_STAGE_KEYS),
                )
                self.assertEqual(
                    [stage["index"] for stage in payload["stages"]],
                    [1, 2, 3, 4, 5, 6, 7],
                )
                self.assertIn(payload["current"]["key"], WORKFLOW_STAGE_INDEX)
                self.assertEqual(
                    payload["current"]["label"],
                    WORKFLOW_STAGE_LABELS[payload["current"]["key"]],
                )

    def test_draft_is_the_quote_stage(self):
        job = self.make_job(
            status=ManagedJobStatus.DRAFT,
            payment_status=ManagedJobPaymentStatus.PENDING,
        )
        payload = self.workflow(job)
        self.assertEqual(payload["current"]["key"], "quote")
        self.assertEqual(payload["current"]["substate"], "Draft")
        self.assertEqual(payload["stages"][0]["state"], "current")

    def test_quoted_is_the_quote_stage(self):
        job = self.make_job(
            status=ManagedJobStatus.QUOTED,
            payment_status=ManagedJobPaymentStatus.PENDING,
        )
        payload = self.workflow(job)
        self.assertEqual(payload["current"]["key"], "quote")
        self.assertEqual(payload["current"]["substate"], "Quote sent")
        self.assertEqual(payload["stages"][1]["state"], "upcoming")

    def test_awaiting_payment_without_artwork_is_the_artwork_stage(self):
        job = self.make_job(
            status=ManagedJobStatus.AWAITING_PAYMENT,
            payment_status=ManagedJobPaymentStatus.PENDING,
        )
        payload = self.workflow(job)
        self.assertEqual(payload["current"]["key"], "artwork")
        self.assertEqual(payload["current"]["substate"], "Awaiting artwork")
        self.assertEqual(payload["stages"][0]["state"], "done")
        self.assertEqual(payload["stages"][2]["state"], "upcoming")

    def test_awaiting_payment_with_artwork_is_the_payment_stage(self):
        job = self.make_job(
            status=ManagedJobStatus.AWAITING_PAYMENT,
            payment_status=ManagedJobPaymentStatus.PENDING,
            workflow_metadata={
                "artwork_workflow": {
                    "source": "manager_holds",
                    "claim_state": "accepted",
                }
            },
        )
        payload = self.workflow(job)
        self.assertEqual(payload["current"]["key"], "payment")
        self.assertEqual(payload["current"]["substate"], "Awaiting payment")
        self.assertEqual(payload["stages"][1]["state"], "done")
        self.assertEqual(payload["stages"][2]["state"], "current")

    def test_payment_confirmed_moves_to_production(self):
        job = self.make_job(
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
        )
        payload = self.workflow(job)
        self.assertEqual(payload["current"]["key"], "production")
        self.assertEqual(payload["current"]["substate"], "Ready to dispatch")
        self.assertEqual(payload["stages"][2]["state"], "done")
        self.assertEqual(payload["stages"][3]["state"], "current")

    def test_printer_quality_check_is_the_qc_stage(self):
        job, _assignment = self.make_qc_job()
        payload = self.workflow(job)
        self.assertEqual(payload["current"]["key"], "qc")
        self.assertEqual(payload["current"]["substate"], "Quality check")
        self.assertEqual(payload["stages"][3]["state"], "done")
        self.assertEqual(payload["stages"][4]["state"], "done")
        self.assertEqual(payload["stages"][5]["state"], "current")

    def test_ready_is_the_delivery_stage(self):
        job = self.make_job(
            status=ManagedJobStatus.READY,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
        )
        payload = self.workflow(job)
        self.assertEqual(payload["current"]["key"], "delivery")
        self.assertEqual(payload["current"]["substate"], "Ready for dispatch")
        self.assertEqual(payload["stages"][5]["state"], "done")

    def test_delivered_keeps_delivery_as_the_anchor(self):
        job = self.make_job(
            status=ManagedJobStatus.DELIVERED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
        )
        payload = self.workflow(job)
        self.assertEqual(payload["current"]["key"], "delivery")
        self.assertEqual(payload["current"]["substate"], "Delivered")
        self.assertFalse(payload["completed"])

    def test_completed_marks_every_stage_done(self):
        job = self.make_job(
            status=ManagedJobStatus.COMPLETED,
            payment_status=ManagedJobPaymentStatus.RELEASED,
        )
        payload = self.workflow(job)
        self.assertTrue(payload["completed"])
        self.assertTrue(all(stage["state"] == "done" for stage in payload["stages"]))
        self.assertEqual(payload["current"]["substate"], "Completed")

    def test_card_projection_includes_the_workflow(self):
        job = self.make_job(
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
        )
        card = build_job_card(job, viewer_role="buyer", user=self.buyer)
        item = card["jobs"][0]
        self.assertIn("workflow", item)
        self.assertEqual(len(item["workflow"]["stages"]), 7)
        self.assertEqual(item["workflow"]["current"]["key"], "production")

    def test_buyer_projection_never_names_the_printer_in_the_workflow(self):
        job = self.make_job(
            status=ManagedJobStatus.IN_PRODUCTION,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
        )
        self.make_assignment(job)
        payload = self.workflow(job, "buyer")
        self.assertEqual(payload["current"]["key"], "production")
        self.assertNotIn("printer", str(payload).lower())
        self.assertNotIn(str(self.shop.name).lower(), str(payload).lower())
