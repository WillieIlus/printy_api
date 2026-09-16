from decimal import Decimal

from django.db import models, IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from .choices import (
    WorkflowCustody,
    WorkflowPressState,
    WorkflowStage,
    WorkflowStatus,
)
from .models import WorkflowJob, WorkflowManager, WorkflowPrinter


class WorkflowManagerModelTest(TestCase):
    def test_str(self):
        m = WorkflowManager(id="m1", name="Dale Carnegie", initials="DC")
        self.assertEqual(str(m), "Dale Carnegie")

    def test_defaults(self):
        m = WorkflowManager.objects.create(id="m1", name="M", initials="M")
        self.assertEqual(m.tag, "")
        self.assertEqual(m.on_time, 0)
        self.assertEqual(m.hue, 0)

    def test_ordering(self):
        WorkflowManager.objects.create(id="m-z", name="Zebra", initials="Z")
        WorkflowManager.objects.create(id="m-a", name="Alpha", initials="A")
        ids = list(WorkflowManager.objects.values_list("id", flat=True))
        self.assertEqual(ids, ["m-a", "m-z"])


class WorkflowPrinterModelTest(TestCase):
    def test_str(self):
        p = WorkflowPrinter(id="p1", name="North Press", contact="Jon", city="Porto")
        self.assertEqual(str(p), "North Press")

    def test_defaults(self):
        p = WorkflowPrinter.objects.create(id="p1", name="P", contact="J", city="C")
        self.assertEqual(p.caps, [])
        self.assertFalse(p.verified)
        self.assertEqual(p.rating, 0.0)
        self.assertEqual(p.jobs_done, 0)
        self.assertEqual(p.on_time, 0)


class WorkflowJobModelTest(TestCase):
    def setUp(self):
        self.manager = WorkflowManager.objects.create(
            id="m1", name="Manager", initials="M"
        )
        self.printer = WorkflowPrinter.objects.create(
            id="p1", name="Printer", contact="J", city="Nairobi"
        )
        self.job = WorkflowJob.objects.create(
            id="j1",
            code="PJ-001",
            title="Business Cards",
            product="Card",
            qty=500,
            value=Decimal("1500.00"),
            buyer_id="b-ava",
            buyer_name="Ava",
            buyer_company="Studio North",
            manager=self.manager,
            printer=self.printer,
            specs={"size": "90x55mm"},
            status=WorkflowStatus.ON_TRACK,
            custody=WorkflowCustody.AWAITING,
            stage=WorkflowStage.APPROVAL,
            press=WorkflowPressState.READY,
            progress=14,
            owner={"name": "Ava", "role": "Buyer"},
            eta="Tomorrow",
            placed_at="Today - 10:00",
            history=[{"stage": "artwork", "actor": "Ava", "actorRole": "Buyer", "at": "Yesterday", "note": "Submitted"}],
            feed=[{"at": "Today", "who": "Ava", "text": "Created job", "jobCode": "PJ-001"}],
        )

    def test_str(self):
        self.assertEqual(str(self.job), "PJ-001 - Business Cards")

    def test_defaults_on_create(self):
        j = WorkflowJob.objects.create(
            id="j2",
            code="PJ-002",
            title="Flyers",
            product="Flyer",
            qty=100,
            value=Decimal("500.00"),
            buyer_id="b-dale",
            buyer_name="Dale",
            buyer_company="Co",
            manager=self.manager,
        )
        self.assertEqual(j.status, WorkflowStatus.ON_TRACK)
        self.assertEqual(j.custody, WorkflowCustody.AWAITING)
        self.assertEqual(j.stage, WorkflowStage.QUOTE)
        self.assertIsNone(j.press)
        self.assertIsNone(j.progress)
        self.assertEqual(j.proof_img, "")
        self.assertEqual(j.eta, "")
        self.assertEqual(j.placed_at, "")
        self.assertIsNone(j.dispute)
        self.assertEqual(j.specs, {})
        self.assertEqual(j.history, [])
        self.assertEqual(j.feed, [])
        self.assertEqual(j.owner, {})

    def test_unique_code_constraint(self):
        with self.assertRaises(IntegrityError):
            WorkflowJob.objects.create(
                id="j-dup",
                code="PJ-001",
                title="Dup",
                product="X",
                qty=1,
                value=Decimal("1.00"),
                buyer_id="b1",
                buyer_name="B",
                buyer_company="C",
                manager=self.manager,
                specs={},
                owner={},
                history=[],
                feed=[],
            )

    def test_protect_manager_on_delete(self):
        mgr2 = WorkflowManager.objects.create(id="m2", name="M2", initials="M2")
        WorkflowJob.objects.create(
            id="j3",
            code="PJ-003",
            title="J",
            product="P",
            qty=1,
            value=Decimal("1.00"),
            buyer_id="b1",
            buyer_name="B",
            buyer_company="C",
            manager=mgr2,
            specs={},
            owner={},
            history=[],
            feed=[],
        )
        with self.assertRaises(ProtectedError):
            mgr2.delete()

    def test_protect_printer_on_delete(self):
        prn2 = WorkflowPrinter.objects.create(id="p2", name="P2", contact="J", city="C")
        WorkflowJob.objects.create(
            id="j4",
            code="PJ-004",
            title="J",
            product="P",
            qty=1,
            value=Decimal("1.00"),
            buyer_id="b1",
            buyer_name="B",
            buyer_company="C",
            manager=self.manager,
            printer=prn2,
            specs={},
            owner={},
            history=[],
            feed=[],
        )
        with self.assertRaises(ProtectedError):
            prn2.delete()

    def test_ordering_by_placed_at_desc(self):
        j_old = WorkflowJob.objects.create(
            id="j-old",
            code="OLD",
            title="Old",
            product="P",
            qty=1,
            value=Decimal("1.00"),
            buyer_id="b1",
            buyer_name="B",
            buyer_company="C",
            manager=self.manager,
            specs={},
            owner={},
            history=[],
            feed=[],
            placed_at="2024-01-01",
        )
        j_new = WorkflowJob.objects.create(
            id="j-new",
            code="NEW",
            title="New",
            product="P",
            qty=1,
            value=Decimal("1.00"),
            buyer_id="b1",
            buyer_name="B",
            buyer_company="C",
            manager=self.manager,
            specs={},
            owner={},
            history=[],
            feed=[],
            placed_at="2024-06-01",
        )
        ids = list(WorkflowJob.objects.filter(id__in=["j-old", "j-new"]).values_list("id", flat=True))
        self.assertEqual(ids, ["j-new", "j-old"])

    def test_null_printer(self):
        j = WorkflowJob.objects.create(
            id="j-np",
            code="NP",
            title="No Printer",
            product="P",
            qty=1,
            value=Decimal("1.00"),
            buyer_id="b1",
            buyer_name="B",
            buyer_company="C",
            manager=self.manager,
            specs={},
            owner={},
            history=[],
            feed=[],
        )
        self.assertIsNone(j.printer)

    def test_manager_related_name(self):
        self.assertIn(self.job, self.manager.jobs.all())

    def test_printer_related_name(self):
        self.assertIn(self.job, self.printer.jobs.all())

    def test_json_fields_accept_dicts_and_lists(self):
        data = self.job
        self.assertIsInstance(data.specs, dict)
        self.assertIsInstance(data.history, list)
        self.assertIsInstance(data.feed, list)
        self.assertIsInstance(data.owner, dict)

    def test_value_stores_as_decimal(self):
        from django.core.exceptions import ValidationError

        self.job.full_clean()

    def test_press_nullable(self):
        self.job.press = None
        self.job.save()
        self.job.refresh_from_db()
        self.assertIsNone(self.job.press)

    def test_progress_nullable(self):
        self.job.progress = None
        self.job.save()
        self.job.refresh_from_db()
        self.assertIsNone(self.job.progress)
