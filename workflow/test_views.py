from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from .models import WorkflowJob, WorkflowManager, WorkflowPrinter
from .views import seed_workflow


class WorkflowSeedTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()

    def test_seed_creates_managers(self):
        seed_workflow()
        self.assertGreaterEqual(WorkflowManager.objects.count(), 1)

    def test_seed_creates_printers(self):
        seed_workflow()
        self.assertGreaterEqual(WorkflowPrinter.objects.count(), 1)

    def test_seed_creates_jobs(self):
        seed_workflow()
        self.assertGreaterEqual(WorkflowJob.objects.count(), 1)

    def test_seed_is_idempotent(self):
        seed_workflow()
        ids_before = list(WorkflowJob.objects.values_list("id", flat=True))
        seed_workflow()
        ids_after = list(WorkflowJob.objects.values_list("id", flat=True))
        self.assertEqual(ids_before, ids_after)


class WorkflowManagerViewSetTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_list_managers(self):
        resp = self.client_obj.get("/api/workflow/managers/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 1)
        keys = set(data[0].keys())
        self.assertIn("id", keys)
        self.assertIn("name", keys)
        self.assertIn("onTime", keys)


class WorkflowPrinterViewSetTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_list_printers(self):
        resp = self.client_obj.get("/api/workflow/printers/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 1)
        keys = set(data[0].keys())
        self.assertIn("jobsDone", keys)
        self.assertIn("onTime", keys)
        self.assertIn("caps", keys)


class WorkflowJobListRetrieveTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_list_jobs(self):
        resp = self.client_obj.get("/api/workflow/jobs/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), len(WorkflowJob.objects.all()))

    def test_retrieve_job(self):
        resp = self.client_obj.get("/api/workflow/jobs/j1/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], "j1")
        self.assertIn("code", data)
        self.assertIn("title", data)

    def test_retrieve_nonexistent_returns_404(self):
        resp = self.client_obj.get("/api/workflow/jobs/j-nonexistent/")
        self.assertEqual(resp.status_code, 404)

    def test_allow_any_no_auth(self):
        resp = self.client_obj.get("/api/workflow/jobs/")
        self.assertEqual(resp.status_code, 200)


class WorkflowApprovalTransitionTest(TestCase):
    """Test approve and request-changes on jobs at the approval stage."""

    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()
        self.j2 = WorkflowJob.objects.get(id="j2")

    def test_approve_artwork(self):
        self.assertEqual(self.j2.stage, "approval")
        resp = self.client_obj.post("/api/workflow/jobs/j2/approve/")
        self.assertEqual(resp.status_code, 200)
        self.j2.refresh_from_db()
        self.assertEqual(self.j2.stage, "payment")
        self.assertEqual(self.j2.status, "on-track")

    def test_approve_from_wrong_stage_returns_409(self):
        resp = self.client_obj.post("/api/workflow/jobs/j1/approve/")
        self.assertEqual(resp.status_code, 409)

    def test_request_changes(self):
        self.assertEqual(self.j2.stage, "approval")
        resp = self.client_obj.post("/api/workflow/jobs/j2/request-changes/")
        self.assertEqual(resp.status_code, 200)
        self.j2.refresh_from_db()
        self.assertEqual(self.j2.stage, "artwork")
        self.assertEqual(self.j2.status, "at-risk")

    def test_request_changes_from_wrong_stage_returns_409(self):
        resp = self.client_obj.post("/api/workflow/jobs/j1/request-changes/")
        self.assertEqual(resp.status_code, 409)


class WorkflowPayTransitionTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()
        self.j3 = WorkflowJob.objects.get(id="j3")

    def test_pay(self):
        self.assertEqual(self.j3.stage, "payment")
        resp = self.client_obj.post("/api/workflow/jobs/j3/pay/")
        self.assertEqual(resp.status_code, 200)
        self.j3.refresh_from_db()
        self.assertEqual(self.j3.stage, "production")
        self.assertEqual(self.j3.custody, "held")

    def test_pay_from_wrong_stage_returns_409(self):
        resp = self.client_obj.post("/api/workflow/jobs/j1/pay/")
        self.assertEqual(resp.status_code, 409)


class WorkflowAssignTransitionTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()
        self.j4 = WorkflowJob.objects.get(id="j4")

    def test_assign_printer(self):
        self.assertEqual(self.j4.stage, "production")
        resp = self.client_obj.post(
            "/api/workflow/jobs/j4/assign/", {"printer_id": "p-north"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.j4.refresh_from_db()
        self.assertEqual(self.j4.stage, "printing")
        self.assertEqual(self.j4.printer_id, "p-north")

    def test_assign_missing_printer_id_returns_400(self):
        resp = self.client_obj.post("/api/workflow/jobs/j4/assign/", format="json")
        self.assertEqual(resp.status_code, 400)

    def test_assign_unknown_printer_returns_400(self):
        resp = self.client_obj.post(
            "/api/workflow/jobs/j4/assign/", {"printer_id": "p-nonexistent"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_assign_from_wrong_stage_returns_409(self):
        resp = self.client_obj.post(
            "/api/workflow/jobs/j1/assign/", {"printer_id": "p-north"}, format="json"
        )
        self.assertEqual(resp.status_code, 409)

    def test_assign_already_assigned_returns_409(self):
        j1 = WorkflowJob.objects.get(id="j1")
        self.assertEqual(j1.printer_id, "p-north")
        resp = self.client_obj.post(
            "/api/workflow/jobs/j1/assign/", {"printer_id": "p-north"}, format="json"
        )
        self.assertEqual(resp.status_code, 409)


class WorkflowPressAdvanceTransitionTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_advance_press_from_ready(self):
        resp = self.client_obj.post("/api/workflow/jobs/j1/press-advance/")
        self.assertEqual(resp.status_code, 200)
        j = WorkflowJob.objects.get(id="j1")
        self.assertEqual(j.press, "active")
        self.assertEqual(j.stage, "printing")

    def test_advance_press_no_match_returns_409(self):
        resp = self.client_obj.post("/api/workflow/jobs/j5/press-advance/")
        self.assertEqual(resp.status_code, 409)


class WorkflowConfirmDeliveryTransitionTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_confirm_delivery(self):
        self.assertEqual(WorkflowJob.objects.get(id="j5").stage, "delivery")
        resp = self.client_obj.post("/api/workflow/jobs/j5/confirm-delivery/")
        self.assertEqual(resp.status_code, 200)
        j = WorkflowJob.objects.get(id="j5")
        self.assertEqual(j.stage, "completed")
        self.assertEqual(j.status, "completed")
        self.assertEqual(j.custody, "released")

    def test_confirm_delivery_wrong_stage_returns_409(self):
        resp = self.client_obj.post("/api/workflow/jobs/j1/confirm-delivery/")
        self.assertEqual(resp.status_code, 409)


class WorkflowResolveDisputeTransitionTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_resolve_dispute(self):
        j = WorkflowJob.objects.get(id="j6")
        self.assertEqual(j.status, "disputed")
        resp = self.client_obj.post("/api/workflow/jobs/j6/resolve-dispute/")
        self.assertEqual(resp.status_code, 200)
        j.refresh_from_db()
        self.assertEqual(j.status, "on-track")
        self.assertTrue(j.dispute["resolved"])

    def test_resolve_dispute_wrong_status_returns_409(self):
        resp = self.client_obj.post("/api/workflow/jobs/j1/resolve-dispute/")
        self.assertEqual(resp.status_code, 409)


class WorkflowNudgeTransitionTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_nudge_default(self):
        resp = self.client_obj.post("/api/workflow/jobs/j1/nudge/", format="json")
        self.assertEqual(resp.status_code, 200)
        j = WorkflowJob.objects.get(id="j1")
        self.assertGreater(len(j.feed), 0)

    def test_nudge_custom_by(self):
        resp = self.client_obj.post(
            "/api/workflow/jobs/j1/nudge/", {"by": "Test User"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        j = WorkflowJob.objects.get(id="j1")
        self.assertEqual(j.feed[0]["who"], "Test User")


class WorkflowResetTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_reset(self):
        resp = self.client_obj.post("/api/workflow/jobs/reset/", format="json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), len(WorkflowJob.objects.all()))


class WorkflowBrowsableApiTest(TestCase):
    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_detail_browsable_has_workflow_panel(self):
        resp = self.client_obj.get("/api/workflow/jobs/j1/", HTTP_ACCEPT="text/html")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Workflow transitions", html)
        self.assertIn("workflow-button-form", html)
        self.assertIn("Approve artwork", html)

    def test_detail_browsable_has_select_for_assign(self):
        resp = self.client_obj.get("/api/workflow/jobs/j1/", HTTP_ACCEPT="text/html")
        html = resp.content.decode()
        self.assertIn('name="printer_id"', html)
        self.assertIn("p-north", html)

    def test_detail_browsable_has_nudge_input(self):
        resp = self.client_obj.get("/api/workflow/jobs/j1/", HTTP_ACCEPT="text/html")
        html = resp.content.decode()
        self.assertIn('name="by"', html)

    def test_list_browsable_has_reset_button(self):
        resp = self.client_obj.get("/api/workflow/jobs/", HTTP_ACCEPT="text/html")
        html = resp.content.decode()
        self.assertIn("Reset demo jobs", html)

    def test_browsable_has_csrf_token(self):
        resp = self.client_obj.get("/api/workflow/jobs/j1/", HTTP_ACCEPT="text/html")
        html = resp.content.decode()
        self.assertIn("csrfmiddlewaretoken", html)


class WorkflowFullFlowTest(TestCase):
    """End-to-end: approve -> pay -> assign -> press x7 -> confirm-delivery."""

    def setUp(self):
        self.client_obj = APIClient()
        seed_workflow()

    def test_full_flow_j2(self):
        approve = self.client_obj.post("/api/workflow/jobs/j2/approve/")
        self.assertEqual(approve.status_code, 200)

        pay = self.client_obj.post("/api/workflow/jobs/j2/pay/")
        self.assertEqual(pay.status_code, 200)

        assign = self.client_obj.post(
            "/api/workflow/jobs/j2/assign/", {"printer_id": "p-north"}, format="json"
        )
        self.assertEqual(assign.status_code, 200)

        for i in range(7):
            resp = self.client_obj.post("/api/workflow/jobs/j2/press-advance/")
            self.assertEqual(resp.status_code, 200, f"press-advance #{i+1} failed")

        delivery = self.client_obj.post("/api/workflow/jobs/j2/confirm-delivery/")
        self.assertEqual(delivery.status_code, 200)

        j = WorkflowJob.objects.get(id="j2")
        self.assertEqual(j.stage, "completed")
        self.assertEqual(j.status, "completed")
        self.assertEqual(j.custody, "released")
