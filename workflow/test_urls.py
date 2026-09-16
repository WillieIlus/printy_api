from django.urls import NoReverseMatch, reverse, resolve
from django.test import SimpleTestCase


class WorkflowUrlTest(SimpleTestCase):
    def test_job_list_url(self):
        self.assertEqual(reverse("workflow-job-list"), "/api/workflow/jobs/")

    def test_job_detail_url(self):
        self.assertEqual(reverse("workflow-job-detail", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/")

    def test_job_approve_url(self):
        self.assertEqual(reverse("workflow-job-approve", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/approve/")

    def test_job_request_changes_url(self):
        self.assertEqual(reverse("workflow-job-request-changes", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/request-changes/")

    def test_job_pay_url(self):
        self.assertEqual(reverse("workflow-job-pay", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/pay/")

    def test_job_assign_url(self):
        self.assertEqual(reverse("workflow-job-assign", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/assign/")

    def test_job_press_advance_url(self):
        self.assertEqual(reverse("workflow-job-press-advance", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/press-advance/")

    def test_job_confirm_delivery_url(self):
        self.assertEqual(reverse("workflow-job-confirm-delivery", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/confirm-delivery/")

    def test_job_resolve_dispute_url(self):
        self.assertEqual(reverse("workflow-job-resolve-dispute", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/resolve-dispute/")

    def test_job_nudge_url(self):
        self.assertEqual(reverse("workflow-job-nudge", kwargs={"pk": "j1"}), "/api/workflow/jobs/j1/nudge/")

    def test_manager_list_url(self):
        self.assertEqual(reverse("workflow-manager-list"), "/api/workflow/managers/")

    def test_manager_detail_url(self):
        self.assertEqual(reverse("workflow-manager-detail", kwargs={"pk": "m1"}), "/api/workflow/managers/m1/")

    def test_printer_list_url(self):
        self.assertEqual(reverse("workflow-printer-list"), "/api/workflow/printers/")

    def test_printer_detail_url(self):
        self.assertEqual(reverse("workflow-printer-detail", kwargs={"pk": "p1"}), "/api/workflow/printers/p1/")

    def test_all_job_action_urls_resolve(self):
        for name in [
            "workflow-job-list",
            "workflow-job-detail",
            "workflow-job-approve",
            "workflow-job-request-changes",
            "workflow-job-pay",
            "workflow-job-assign",
            "workflow-job-press-advance",
            "workflow-job-confirm-delivery",
            "workflow-job-resolve-dispute",
            "workflow-job-nudge",
        ]:
            if name == "workflow-job-list":
                reverse(name)
            else:
                reverse(name, kwargs={"pk": "j1"})

    def test_workflow_urls_are_mounted(self):
        resolver = resolve("/api/workflow/jobs/")
        self.assertEqual(resolver.func.cls.__name__, "WorkflowJobViewSet")