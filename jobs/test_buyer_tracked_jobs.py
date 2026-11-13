"""Regression tests for signed-in buyer live tracking."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import ManagedJob
from quotes.models import QuoteRequest
from shops.models import Shop

URL = "/api/dashboard/client/tracked-jobs/"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BuyerTrackedJobsTestCase(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.buyer = self._buyer("buyer-tracked@example.com")
        self.other_buyer = self._buyer("other-tracked@example.com")
        self.manager = User.objects.create_user(
            email="manager-tracked@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(
            email="printer-tracked@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.printer,
            name="Hidden Secret Print Shop",
            slug="hidden-secret-print-shop",
            is_active=True,
        )

    def _buyer(self, email):
        return User.objects.create_user(email=email, password="pass12345", role=User.Role.CLIENT)

    def _job(self, *, buyer=None, status=ManagedJobStatus.PAYMENT_CONFIRMED, payment_status=ManagedJobPaymentStatus.CONFIRMED, **kwargs):
        defaults = {
            "title": "Tracked print job",
            "client": buyer or self.buyer,
            "created_by": buyer or self.buyer,
            "broker": self.manager,
            "assigned_shop": self.shop,
            "status": status,
            "payment_status": payment_status,
            "client_total": Decimal("2500.00"),
            "payment_confirmed_at": timezone.now(),
            "due_date": timezone.now() + timedelta(hours=80),
        }
        defaults.update(kwargs)
        return ManagedJob.objects.create(**defaults)

    def _get(self, user=None):
        self.api_client.force_authenticate(user or self.buyer)
        return self.api_client.get(URL)

    def test_owner_sees_own_paid_job_with_buyer_stages(self):
        job = self._job()
        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        row = body["results"][0]
        self.assertEqual(row["job_code"], job.managed_reference)
        self.assertEqual(row["current_stage"], "Artwork check")
        self.assertEqual(
            [stage["label"] for stage in row["stages"]],
            [
                "Payment received",
                "Artwork check",
                "Printing",
                "Finishing",
                "Quality check",
                "Out for delivery",
                "Delivered",
            ],
        )
        self.assertEqual(row["stages"][0]["status"], "done")
        self.assertEqual(row["stages"][1]["status"], "current")
        self.assertIn("estimated_remaining_minutes", row)
        self.assertIn("last_updated_at", row)

    def test_job_code_uses_the_immutable_quote_reference(self):
        quote_request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Tracked Buyer",
            customer_email="buyer-tracked@example.com",
            customer_phone="+254700111222",
            request_reference="QR-TRACK-0001",
        )
        self._job(source_quote_request=quote_request)
        row = self._get().json()["results"][0]
        self.assertEqual(row["job_code"], "QR-TRACK-0001")

    def test_dashboard_job_code_opens_the_public_full_timeline(self):
        quote_request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Tracked Buyer",
            customer_email="buyer-tracked@example.com",
            customer_phone="+254700111222",
            request_reference="QR-TRACK-0002",
        )
        self._job(source_quote_request=quote_request)
        row = self._get().json()["results"][0]
        self.assertEqual(row["job_code"], "QR-TRACK-0002")

        # What the dashboard's "Full timeline" NuxtLink does: /track/{job_code}
        # becomes a public-tracking request with the code lowercased.
        response = self.api_client.get(
            f"/api/public/managed-jobs/track/{row['job_code'].lower()}/"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], row["job_code"])
        self.assertIn("stages", body["timeline"])

    def test_full_timeline_detail_loads_for_the_owning_buyer(self):
        job = self._job()
        self.api_client.force_authenticate(self.buyer)
        response = self.api_client.get(f"/api/dashboard/client/tracked-jobs/{job.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["job"]["id"], job.id)
        self.assertEqual(body["job"]["job_code"], job.managed_reference)
        self.assertEqual(body["tracking_reference"], job.managed_reference)
        self.assertNotIn("_due_sort", body["job"])
        self.assertNotIn("_delivered_sort", body["job"])
        self.assertNotIn("_job_id", body["job"])
        self.assertTrue(body["timeline"]["stages"])
        self.assertIn("current_stage", body["timeline"])
        # The signed-in view gets stage names and clocks, not the public
        # tracker's internal owner/next-action fields.
        first_stage = body["timeline"]["stages"][0]
        self.assertNotIn("owner", first_stage)
        self.assertNotIn("next_action", first_stage)
        # The same buyer projection as the dashboard: no shop, cost or margin.
        content = response.content.decode()
        self.assertNotIn(self.shop.name, content)
        self.assertNotIn(self.manager.email, content)
        self.assertNotIn("client_total", content)
        self.assertNotIn("production_cost", content)

    def test_full_timeline_detail_uses_the_buyer_visible_job_code(self):
        quote_request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Tracked Buyer",
            customer_email="buyer-tracked@example.com",
            customer_phone="+254700111222",
            request_reference="QR-TRACK-0003",
        )
        job = self._job(source_quote_request=quote_request)
        self.api_client.force_authenticate(self.buyer)
        response = self.api_client.get(f"/api/dashboard/client/tracked-jobs/{job.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["job"]["job_code"], "QR-TRACK-0003")
        self.assertEqual(body["tracking_reference"], "QR-TRACK-0003")
        self.assertTrue(body["tracking_url"].endswith("/track/QR-TRACK-0003"))

    def test_full_timeline_detail_is_scoped_to_the_owner(self):
        job = self._job()
        self.api_client.force_authenticate(self.other_buyer)
        response = self.api_client.get(f"/api/dashboard/client/tracked-jobs/{job.id}/")
        self.assertEqual(response.status_code, 404)

    def test_full_timeline_detail_rejects_non_buyer_roles(self):
        job = self._job()
        for user in (self.manager, self.printer):
            self.api_client.force_authenticate(user)
            response = self.api_client.get(f"/api/dashboard/client/tracked-jobs/{job.id}/")
            self.assertEqual(response.status_code, 403)

    def test_full_timeline_detail_hides_unpaid_jobs(self):
        job = self._job(payment_status=ManagedJobPaymentStatus.PENDING)
        self.api_client.force_authenticate(self.buyer)
        response = self.api_client.get(f"/api/dashboard/client/tracked-jobs/{job.id}/")
        self.assertEqual(response.status_code, 404)

    def test_full_timeline_works_without_a_quote_reference(self):
        job = self._job()
        self.api_client.force_authenticate(self.buyer)
        response = self.api_client.get(f"/api/dashboard/client/tracked-jobs/{job.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["job"]["job_code"], job.managed_reference)
        self.assertTrue(body["tracking_url"].endswith(f"/track/{job.managed_reference}"))
        self.assertTrue(body["timeline"]["stages"])

    def test_unpaid_job_is_not_returned(self):
        self._job(payment_status=ManagedJobPaymentStatus.PENDING)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_another_buyer_cannot_see_the_job(self):
        self._job()
        response = self._get(user=self.other_buyer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_printer_and_manager_identity_and_costs_are_absent(self):
        self._job(title="Secret shop job")
        response = self._get()
        content = response.content.decode()
        self.assertNotIn(self.shop.name, content)
        self.assertNotIn(self.manager.email, content)
        self.assertNotIn("client_total", content)
        self.assertNotIn("assigned_shop", content)
        self.assertNotIn("production_cost", content)
        self.assertNotIn("printy_fee", content)

    def test_late_job_is_flagged_and_sorted_first(self):
        late = self._job(due_date=timezone.now() - timedelta(hours=2), is_late=True, lateness_minutes=120)
        self._job(due_date=timezone.now() + timedelta(hours=2))
        response = self._get()
        results = response.json()["results"]
        self.assertTrue(results[0]["is_late"])
        self.assertEqual(results[0]["job_code"], late.managed_reference)
        self.assertEqual(results[0]["estimated_remaining_minutes"], 0)
        self.assertGreaterEqual(results[0]["overdue_minutes"], 120)

    def test_delivered_job_disappears_after_24_hours(self):
        old = self._job(
            status=ManagedJobStatus.DELIVERED,
            delivered_at=timezone.now() - timedelta(hours=25),
        )
        recent = self._job(
            status=ManagedJobStatus.DELIVERED,
            delivered_at=timezone.now() - timedelta(hours=1),
        )
        response = self._get()
        codes = [row["job_code"] for row in response.json()["results"]]
        self.assertNotIn(old.managed_reference, codes)
        self.assertIn(recent.managed_reference, codes)
        self.assertEqual(response.json()["results"][0]["current_stage"], "Delivered")

    def test_remaining_time_recalculates_after_a_stage_completes(self):
        job = self._job(
            status=ManagedJobStatus.IN_PRODUCTION,
            production_started_at=timezone.now() - timedelta(hours=2),
        )
        before = self._get().json()["results"][0]["estimated_remaining_minutes"]
        job.status = ManagedJobStatus.READY
        job.ready_at = timezone.now()
        job.save(update_fields=["status", "ready_at", "updated_at"])
        after = self._get().json()["results"][0]["estimated_remaining_minutes"]
        self.assertLess(after, before)
        self.assertEqual(after, 16 * 60)

    def test_remaining_time_drops_when_printing_completes(self):
        job = self._job(
            status=ManagedJobStatus.IN_PRODUCTION,
            production_started_at=timezone.now() - timedelta(hours=2),
        )
        before = self._get().json()["results"][0]["estimated_remaining_minutes"]
        job.status = ManagedJobStatus.FINISHING
        job.save(update_fields=["status", "updated_at"])
        after = self._get().json()["results"][0]["estimated_remaining_minutes"]
        self.assertLess(after, before)
        self.assertEqual(after, 44 * 60)

    def test_finishing_substeps_come_from_the_job_snapshot(self):
        quote_request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Tracked Buyer",
            customer_email="buyer-tracked@example.com",
            customer_phone="+254700111222",
            request_snapshot={
                "request_snapshot": {
                    "finishings": [{"label": "Cutting"}, {"name": "Lamination"}],
                    "binding_type": "Saddle stitch",
                }
            },
        )
        self._job(source_quote_request=quote_request, status=ManagedJobStatus.FINISHING)
        row = self._get().json()["results"][0]
        labels = [step["label"] for step in row["finishing_steps"]]
        self.assertIn("Cutting", labels)
        self.assertIn("Lamination", labels)
        self.assertIn("Saddle stitch", labels)
        self.assertEqual(row["current_stage"], "Finishing")
        self.assertTrue(all(step["status"] == "current" for step in row["finishing_steps"]))

    def test_stage_mapping_for_printing_and_delivery(self):
        self._job(status=ManagedJobStatus.IN_PRODUCTION)
        self.assertEqual(self._get().json()["results"][0]["current_stage"], "Printing")
        ManagedJob.objects.all().delete()
        self._job(status=ManagedJobStatus.DELIVERED, delivered_at=timezone.now())
        self.assertEqual(self._get().json()["results"][0]["current_stage"], "Delivered")

    def test_tracker_returns_plain_stage_note_position_and_state(self):
        self._job(status=ManagedJobStatus.IN_PRODUCTION)
        row = self._get().json()["results"][0]
        self.assertEqual(row["stage_note"], "Printing")
        self.assertEqual(row["stage_number"], 3)
        self.assertEqual(row["stage_count"], 7)
        self.assertEqual(row["state"], "on_track")

    def test_finishing_stage_note_names_the_actual_finishing_step(self):
        quote_request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Tracked Buyer",
            customer_email="buyer-tracked@example.com",
            customer_phone="+254700111222",
            request_snapshot={"request_snapshot": {"finishings": [{"label": "Lamination"}]}},
        )
        self._job(source_quote_request=quote_request, status=ManagedJobStatus.FINISHING)
        row = self._get().json()["results"][0]
        self.assertEqual(row["stage_note"], "Being laminated")
        self.assertEqual(row["stage_number"], 4)

    def test_late_and_delivered_states_are_explicit(self):
        self._job(due_date=timezone.now() - timedelta(hours=1), is_late=True)
        self.assertEqual(self._get().json()["results"][0]["state"], "late")
        ManagedJob.objects.all().delete()
        self._job(status=ManagedJobStatus.DELIVERED, delivered_at=timezone.now())
        self.assertEqual(self._get().json()["results"][0]["state"], "delivered")

    def test_non_buyer_roles_are_forbidden(self):
        self._job()
        for user in (self.manager, self.printer):
            response = self._get(user=user)
            self.assertEqual(response.status_code, 403)
