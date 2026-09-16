from decimal import Decimal

from django.test import TestCase

from .serializers import WorkflowJobSerializer, WorkflowManagerSerializer, WorkflowPrinterSerializer
from .models import WorkflowJob, WorkflowManager, WorkflowPrinter


class WorkflowManagerSerializerTest(TestCase):
    def setUp(self):
        self.mgr = WorkflowManager.objects.create(id="m1", name="Dale Carnegie", initials="DC", tag="Client whisperer", on_time=96, hue=36)

    def test_fields(self):
        data = WorkflowManagerSerializer(self.mgr).data
        self.assertEqual(set(data.keys()), {"id", "name", "initials", "tag", "onTime", "hue"})

    def test_camel_case(self):
        data = WorkflowManagerSerializer(self.mgr).data
        self.assertIn("onTime", data)
        self.assertNotIn("on_time", data)
        self.assertEqual(data["onTime"], 96)

    def test_values(self):
        data = WorkflowManagerSerializer(self.mgr).data
        self.assertEqual(data["id"], "m1")
        self.assertEqual(data["name"], "Dale Carnegie")
        self.assertEqual(data["initials"], "DC")
        self.assertEqual(data["hue"], 36)


class WorkflowPrinterSerializerTest(TestCase):
    def setUp(self):
        self.prn = WorkflowPrinter.objects.create(
            id="p1", name="North Press", contact="Jon Weber", city="Porto",
            caps=["Offset", "Digital"], verified=True, rating=4.9,
            jobs_done=212, on_time=98, initials="NP", hue=32,
        )

    def test_fields(self):
        data = WorkflowPrinterSerializer(self.prn).data
        self.assertEqual(set(data.keys()), {
            "id", "name", "contact", "city", "caps", "verified",
            "rating", "jobsDone", "onTime", "initials", "hue",
        })

    def test_camel_case(self):
        data = WorkflowPrinterSerializer(self.prn).data
        self.assertIn("jobsDone", data)
        self.assertIn("onTime", data)
        self.assertNotIn("jobs_done", data)
        self.assertNotIn("on_time", data)

    def test_values(self):
        data = WorkflowPrinterSerializer(self.prn).data
        self.assertEqual(data["jobsDone"], 212)
        self.assertEqual(data["onTime"], 98)
        self.assertTrue(data["verified"])
        self.assertEqual(data["caps"], ["Offset", "Digital"])
        self.assertAlmostEqual(data["rating"], 4.9)


class WorkflowJobSerializerTest(TestCase):
    def setUp(self):
        self.manager = WorkflowManager.objects.create(id="m1", name="Manager", initials="M")
        self.printer = WorkflowPrinter.objects.create(id="p1", name="Printer", contact="J", city="Nairobi")

    def _make_job(self, **overrides):
        defaults = dict(
            id="j1", code="PJ-001", title="Job", product="Card", qty=100,
            value=Decimal("1500.00"), buyer_id="b-ava", buyer_name="Ava",
            buyer_company="Studio North", manager=self.manager, printer=self.printer,
            specs={"size": "A4"}, owner={"name": "Ava", "role": "Buyer"},
            history=[], feed=[], stage="approval", status="on-track",
            custody="awaiting", placed_at="Today",
        )
        defaults.update(overrides)
        return WorkflowJob.objects.create(**defaults)

    def test_fields_present(self):
        job = self._make_job()
        data = WorkflowJobSerializer(job).data
        expected = {
            "id", "code", "title", "product", "qty", "value",
            "buyerId", "buyerName", "buyerCompany",
            "managerId", "printerId", "specs", "proofImg",
            "status", "custody", "stage", "press", "progress",
            "owner", "eta", "placedAt", "dispute", "history", "feed",
        }
        self.assertEqual(set(data.keys()), expected)

    def test_camel_case_fields(self):
        job = self._make_job()
        data = WorkflowJobSerializer(job).data
        self.assertIn("buyerId", data)
        self.assertIn("buyerName", data)
        self.assertIn("buyerCompany", data)
        self.assertIn("managerId", data)
        self.assertIn("printerId", data)
        self.assertIn("proofImg", data)
        self.assertIn("placedAt", data)
        self.assertNotIn("buyer_id", data)
        self.assertNotIn("manager_id", data)
        self.assertNotIn("printer_id", data)

    def test_value_is_float(self):
        job = self._make_job(value=Decimal("1234.56"))
        data = WorkflowJobSerializer(job).data
        self.assertIsInstance(data["value"], float)
        self.assertAlmostEqual(data["value"], 1234.56)

    def test_managerId_from_fk(self):
        job = self._make_job()
        data = WorkflowJobSerializer(job).data
        self.assertEqual(data["managerId"], "m1")

    def test_printerId_from_fk(self):
        job = self._make_job()
        data = WorkflowJobSerializer(job).data
        self.assertEqual(data["printerId"], "p1")

    def test_printerId_none_when_no_printer(self):
        job = self._make_job(printer=None)
        data = WorkflowJobSerializer(job).data
        self.assertIsNone(data["printerId"])

    def test_progress_null(self):
        job = self._make_job(progress=None)
        data = WorkflowJobSerializer(job).data
        self.assertIsNone(data["progress"])

    def test_dispute_null(self):
        job = self._make_job(dispute=None)
        data = WorkflowJobSerializer(job).data
        self.assertIsNone(data["dispute"])

    def test_specs_is_dict(self):
        job = self._make_job(specs={"material": "Matte", "colors": 4})
        data = WorkflowJobSerializer(job).data
        self.assertEqual(data["specs"], {"material": "Matte", "colors": 4})

    def test_history_is_list(self):
        job = self._make_job(history=[{"stage": "artwork", "actor": "Ava", "actorRole": "Buyer", "at": "Today", "note": "Submitted"}])
        data = WorkflowJobSerializer(job).data
        self.assertEqual(len(data["history"]), 1)
        self.assertEqual(data["history"][0]["stage"], "artwork")

    def test_feed_is_list(self):
        job = self._make_job(feed=[{"at": "Today", "who": "Ava", "text": "Created", "jobCode": "PJ-001"}])
        data = WorkflowJobSerializer(job).data
        self.assertEqual(len(data["feed"]), 1)

    def test_owner_is_dict(self):
        job = self._make_job(owner={"name": "Ava", "role": "Buyer", "action": "Review artwork", "waitingHrs": 0, "slaHrs": 4})
        data = WorkflowJobSerializer(job).data
        self.assertEqual(data["owner"]["name"], "Ava")
        self.assertEqual(data["owner"]["role"], "Buyer")

    def test_roundtrip_preserves_data(self):
        job = self._make_job(
            stage="production", status="at-risk", custody="held",
            press="active", progress=50, eta="Tomorrow - 14:00",
            placed_at="Today - 10:00",
        )
        data = WorkflowJobSerializer(job).data
        self.assertEqual(data["stage"], "production")
        self.assertEqual(data["status"], "at-risk")
        self.assertEqual(data["custody"], "held")
        self.assertEqual(data["press"], "active")
        self.assertEqual(data["progress"], 50)
        self.assertEqual(data["eta"], "Tomorrow - 14:00")
        self.assertEqual(data["placedAt"], "Today - 10:00")
