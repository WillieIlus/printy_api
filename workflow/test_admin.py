from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .admin import WorkflowJobAdmin, WorkflowManagerAdmin, WorkflowPrinterAdmin
from .models import WorkflowJob, WorkflowManager, WorkflowPrinter
from .views import seed_workflow

User = get_user_model()


class WorkflowAdminRegistrationTest(TestCase):
    def test_manager_registered(self):
        self.assertIn(WorkflowManager, admin.site._registry)

    def test_printer_registered(self):
        self.assertIn(WorkflowPrinter, admin.site._registry)

    def test_job_registered(self):
        self.assertIn(WorkflowJob, admin.site._registry)

    def test_manager_admin_attributes(self):
        model_admin = admin.site._registry[WorkflowManager]
        self.assertEqual(model_admin.list_display, ("id", "name", "initials", "tag", "on_time", "hue"))
        self.assertEqual(model_admin.search_fields, ("name", "tag"))

    def test_printer_admin_attributes(self):
        model_admin = admin.site._registry[WorkflowPrinter]
        self.assertEqual(model_admin.list_display, ("id", "name", "contact", "city", "verified", "rating", "jobs_done", "on_time"))
        self.assertEqual(model_admin.search_fields, ("name", "contact", "city"))

    def test_job_admin_attributes(self):
        model_admin = admin.site._registry[WorkflowJob]
        self.assertEqual(model_admin.list_display, ("id", "code", "title", "stage", "status", "custody", "value"))
        self.assertEqual(model_admin.list_filter, ("stage", "status", "custody"))
        self.assertEqual(model_admin.search_fields, ("code", "title"))

    def test_job_admin_changelist_200(self):
        seed_workflow()
        User.objects.create_superuser(email="wf-admin-job@test.com", password="pass12345")
        c = Client()
        c.login(email="wf-admin-job@test.com", password="pass12345")
        resp = c.get(reverse("admin:workflow_workflowjob_changelist"))
        self.assertEqual(resp.status_code, 200)

    def test_manager_admin_changelist_200(self):
        seed_workflow()
        User.objects.create_superuser(email="wf-admin-mgr@test.com", password="pass12345")
        c = Client()
        c.login(email="wf-admin-mgr@test.com", password="pass12345")
        resp = c.get(reverse("admin:workflow_workflowmanager_changelist"))
        self.assertEqual(resp.status_code, 200)

    def test_printer_admin_changelist_200(self):
        seed_workflow()
        User.objects.create_superuser(email="wf-admin-prn@test.com", password="pass12345")
        c = Client()
        c.login(email="wf-admin-prn@test.com", password="pass12345")
        resp = c.get(reverse("admin:workflow_workflowprinter_changelist"))
        self.assertEqual(resp.status_code, 200)
