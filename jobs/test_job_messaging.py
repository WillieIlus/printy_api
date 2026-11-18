"""Stage-scoped job messaging tests (reuses QuoteRequestMessage)."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from jobs.choices import ManagedJobStatus
from jobs.models import ManagedJob
from notifications.models import Notification
from quotes.models import QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class JobMessagingTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="job-client@example.com", password="pass", role=User.Role.CLIENT, name="Wanjiku Buyer"
        )
        self.manager = User.objects.create_user(
            email="job-manager@example.com", password="pass", role=User.Role.PARTNER, name="Amina Manager"
        )
        self.printer = User.objects.create_user(
            email="job-printer@example.com", password="pass", role=User.Role.PRODUCTION, name="Press Owner"
        )
        self.outsider = User.objects.create_user(
            email="job-outsider@example.com", password="pass", role=User.Role.CLIENT, name="Other Buyer"
        )
        self.shop = Shop.objects.create(owner=self.printer, name="Mwangaza Press", slug="mwangaza-press", is_active=True)
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            shop=self.shop,
            status="submitted",
            customer_name="Wanjiku Buyer",
            customer_email="job-client@example.com",
        )
        self.job = ManagedJob.objects.create(
            title="1,000 business cards",
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            source_quote_request=self.quote_request,
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
        )

    def as_user(self, user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def send(self, user, body, stage="quote_accepted", audience="client"):
        return self.as_user(user).post(
            f"/api/managed-jobs/{self.job.id}/messages/",
            {"body": body, "stage": stage, "audience": audience},
            format="json",
            HTTP_HOST="localhost",
        )

    def test_client_and_manager_conversation_is_linked_to_the_job_stage(self):
        response = self.send(self.client_user, "Can you discount 2,000 copies?")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["stages"][0]["key"], "quote_accepted")
        self.assertEqual(len(response.data["stages"][0]["messages"]), 1)
        response = self.send(self.manager, "Yes, 5% off at 2,000.", stage="quote_accepted")
        self.assertEqual(response.status_code, 201)
        client_bundle = self.as_user(self.client_user).get(
            f"/api/managed-jobs/{self.job.id}/messages/", HTTP_HOST="localhost"
        )
        self.assertEqual(client_bundle.data["stages"][0]["messages"][-1]["sender_name"], "Amina Manager")
        self.assertEqual(
            Notification.objects.filter(
                user=self.client_user, notification_type=Notification.JOB_MESSAGE_RECEIVED
            ).count(),
            1,
        )

    def test_client_cannot_see_manager_printer_conversation(self):
        response = self.send(self.manager, "Print 1,200 not 1,000.", stage="production", audience="printer")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Notification.objects.filter(
                user=self.printer, notification_type=Notification.JOB_MESSAGE_RECEIVED
            ).count(),
            1,
        )
        response = self.send(self.printer, "Noted, 1,200 confirmed.", stage="production", audience="printer")
        self.assertEqual(response.status_code, 201)

        client_bundle = self.as_user(self.client_user).get(
            f"/api/managed-jobs/{self.job.id}/messages/", HTTP_HOST="localhost"
        )
        self.assertEqual(client_bundle.status_code, 200)
        bodies = [m["body"] for stage in client_bundle.data["stages"] for m in stage["messages"]]
        self.assertNotIn("Print 1,200 not 1,000.", bodies)
        self.assertNotIn("Noted, 1,200 confirmed.", bodies)
        self.assertNotIn("Mwangaza Press", str(client_bundle.data))

        printer_bundle = self.as_user(self.printer).get(
            f"/api/managed-jobs/{self.job.id}/messages/", HTTP_HOST="localhost"
        )
        printer_bodies = [m["body"] for stage in printer_bundle.data["stages"] for m in stage["messages"]]
        self.assertIn("Print 1,200 not 1,000.", printer_bodies)
        self.assertIn("Noted, 1,200 confirmed.", printer_bodies)

    def test_printer_cannot_send_to_client_and_client_cannot_send_to_printer(self):
        self.assertEqual(
            self.send(self.printer, "direct to client", stage="production", audience="client").status_code,
            403,
        )
        self.assertEqual(
            self.send(self.client_user, "direct to printer", stage="production", audience="printer").status_code,
            403,
        )

    def test_old_stage_messages_remain_after_the_job_moves_on(self):
        self.send(self.client_user, "Discount request", stage="quote_accepted")
        self.job.status = ManagedJobStatus.IN_PRODUCTION
        self.job.save(update_fields=["status", "updated_at"])
        response = self.as_user(self.manager).get(
            f"/api/managed-jobs/{self.job.id}/messages/", HTTP_HOST="localhost"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stages"][0]["key"], "quote_accepted")
        self.assertEqual(response.data["stages"][0]["messages"][0]["body"], "Discount request")

    def test_unread_and_read_marking_are_scoped_to_the_recipient(self):
        self.send(self.client_user, "A question")
        manager_bundle = self.as_user(self.manager).get(
            f"/api/managed-jobs/{self.job.id}/messages/", HTTP_HOST="localhost"
        )
        self.assertEqual(manager_bundle.data["total_unread"], 1)
        message_id = manager_bundle.data["stages"][0]["messages"][0]["id"]
        read = self.as_user(self.manager).post(
            f"/api/managed-jobs/{self.job.id}/messages/{message_id}/read/", HTTP_HOST="localhost"
        )
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.data["total_unread"], 0)

    def test_outsider_cannot_access_job_messages(self):
        response = self.as_user(self.outsider).get(
            f"/api/managed-jobs/{self.job.id}/messages/", HTTP_HOST="localhost"
        )
        self.assertEqual(response.status_code, 403)
