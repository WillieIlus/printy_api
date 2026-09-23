"""Blocker #2/#3/#4: delivery, client accept-on-finish, and notifications.

Covers the canonical delivery endpoints (/managed-jobs/<pk>/delivery/mark-delivered/
and .../delivery/confirm/) plus the missing event notifications across the
artwork/proof lifecycle.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile

from jobs.choices import ManagedJobStatus
from jobs.file_services import manager_approve_job_proof, upload_proof_for_managed_job
from jobs.models import JobAssignment, ManagedJob
from notifications.models import Notification
from production.models import ProductionOrder
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


User = get_user_model()


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ManagedJobDeliveryCompletionTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client_user = User.objects.create_user(
            email="delivery-client@example.com", password="pass", role=User.Role.CLIENT, name="Delivery Client"
        )
        self.shop_owner = User.objects.create_user(
            email="delivery-shop@example.com", password="pass", role=User.Role.PRODUCTION, name="Delivery Shop Owner"
        )
        self.partner = User.objects.create_user(
            email="delivery-partner@example.com",
            password="pass",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            name="Delivery Manager",
        )
        self.ops = User.objects.create_user(email="delivery-ops@example.com", password="pass", is_staff=True)
        self.shop = Shop.objects.create(
            owner=self.shop_owner, name="Delivery Shop", slug="delivery-shop", is_active=True
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            customer_name="Delivery Client",
            customer_email=self.client_user.email,
            status=QuoteStatus.CLOSED,
            request_snapshot={"request_snapshot": {"product_type": "business_card", "quantity": 100}},
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.partner,
            status=QuoteOfferStatus.ACCEPTED,
            total=Decimal("1800.00"),
        )
        self.managed_job = ManagedJob.objects.create(
            title="Delivery managed job",
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            broker=self.partner,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status=ManagedJobStatus.READY,
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.managed_job,
            assigned_shop=self.shop,
            source_quote=self.quote,
            status="ready",
        )
        self.production_order = ProductionOrder.objects.create(
            shop=self.shop,
            title="Delivery production order",
            quantity=100,
            status=ProductionOrder.READY,
            delivery_status=ProductionOrder.DELIVERY_PENDING,
        )
        self.assignment.production_order = self.production_order
        self.assignment.save(update_fields=["production_order"])

    def _notifications_for(self, user):
        return Notification.objects.filter(
            user=user,
            notification_type=Notification.JOB_STATUS_UPDATED,
            object_type="managed_job",
            object_id=self.managed_job.id,
        )

    def test_shop_can_mark_managed_job_delivered_and_syncs_production_order(self):
        self.client.force_authenticate(user=self.shop_owner)

        response = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/delivery/mark-delivered/",
            {"note": "Handed to rider at 2pm."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "delivered")
        self.managed_job.refresh_from_db()
        self.assertEqual(self.managed_job.status, "delivered")
        self.assertIsNotNone(self.managed_job.delivered_at)
        self.assertEqual(self.managed_job.operational_snapshot.get("delivery_note"), "Handed to rider at 2pm.")

        self.production_order.refresh_from_db()
        self.assertEqual(self.production_order.delivery_status, ProductionOrder.DELIVERY_DELIVERED)
        self.assertIsNotNone(self.production_order.delivered_at)

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, "ready")
        self.assertTrue(self._notifications_for(self.client_user).exists())
        self.assertTrue(self._notifications_for(self.partner).exists())

    def test_ops_can_mark_delivered_but_client_cannot(self):
        self.client.force_authenticate(user=self.ops)
        ops_response = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/delivery/mark-delivered/",
            {},
            format="json",
        )
        self.assertEqual(ops_response.status_code, 200)
        self.managed_job.refresh_from_db()
        self.assertEqual(self.managed_job.status, "delivered")

        self.client.force_authenticate(user=self.client_user)
        forbidden = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/delivery/mark-delivered/",
            {},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_mark_delivered_from_draft_is_rejected(self):
        self.managed_job.status = "draft"
        self.managed_job.save(update_fields=["status"])
        self.client.force_authenticate(user=self.shop_owner)

        response = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/delivery/mark-delivered/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_client_confirms_completion_after_delivery(self):
        self.managed_job.status = "delivered"
        self.managed_job.delivered_at = None
        self.managed_job.save(update_fields=["status"])
        self.client.force_authenticate(user=self.client_user)

        response = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/delivery/confirm/",
            {"note": "Everything checks out."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.managed_job.refresh_from_db()
        self.assertEqual(self.managed_job.status, "completed")
        self.assertIsNotNone(self.managed_job.completed_at)

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, "completed")
        self.production_order.refresh_from_db()
        self.assertEqual(self.production_order.status, ProductionOrder.COMPLETED)
        self.assertEqual(self.production_order.delivery_status, ProductionOrder.DELIVERY_DELIVERED)

        self.assertTrue(self._notifications_for(self.shop_owner).exists())
        self.assertTrue(self._notifications_for(self.partner).exists())

    def test_ops_can_confirm_completion_and_shop_cannot(self):
        self.managed_job.status = "ready"
        self.managed_job.save(update_fields=["status"])
        self.client.force_authenticate(user=self.ops)

        response = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/delivery/confirm/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.managed_job.refresh_from_db()
        self.assertEqual(self.managed_job.status, "completed")

        self.managed_job.status = "delivered"
        self.managed_job.save(update_fields=["status"])
        self.client.force_authenticate(user=self.shop_owner)
        forbidden = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/delivery/confirm/",
            {},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_confirm_completion_from_invalid_state_is_rejected(self):
        self.managed_job.status = "awaiting_payment"
        self.managed_job.save(update_fields=["status"])
        self.client.force_authenticate(user=self.client_user)

        response = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/delivery/confirm/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_assignments_ready_queue_exposes_mark_delivered_action(self):
        self.client.force_authenticate(user=self.shop_owner)

        listing = self.client.get("/api/shop/assignments/")

        self.assertEqual(listing.status_code, 200)
        row = next(item for item in listing.json() if item["id"] == self.assignment.id)
        self.assertIn("mark_delivered", row["next_allowed_actions"])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ManagedJobNotificationWiringTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client_user = User.objects.create_user(
            email="notify-client@example.com", password="pass", role=User.Role.CLIENT
        )
        self.shop_owner = User.objects.create_user(
            email="notify-shop@example.com", password="pass", role=User.Role.PRODUCTION
        )
        self.partner = User.objects.create_user(
            email="notify-partner@example.com",
            password="pass",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.shop = Shop.objects.create(owner=self.shop_owner, name="Notify Shop", slug="notify-shop", is_active=True)
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            customer_name="Notify Client",
            customer_email=self.client_user.email,
            status=QuoteStatus.CLOSED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.partner,
            status=QuoteOfferStatus.ACCEPTED,
            total=Decimal("1000.00"),
        )
        self.managed_job = ManagedJob.objects.create(
            title="Notify managed job",
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            broker=self.partner,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status="ready",
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.managed_job,
            assigned_shop=self.shop,
            source_quote=self.quote,
            status="accepted",
        )

    def _count(self, user):
        return Notification.objects.filter(
            user=user,
            notification_type=Notification.JOB_STATUS_UPDATED,
            object_type="managed_job",
            object_id=self.managed_job.id,
        ).count()

    def test_artwork_upload_notifies_broker_and_shop(self):
        self.client.force_authenticate(user=self.client_user)

        response = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/files/artwork/",
            {"file": SimpleUploadedFile("client-artwork.pdf", b"artwork-bytes", content_type="application/pdf")},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertGreaterEqual(self._count(self.partner), 1)
        self.assertGreaterEqual(self._count(self.shop_owner), 1)
        self.assertEqual(self._count(self.client_user), 0)

    def test_proof_upload_notifies_broker(self):
        self.client.force_authenticate(user=self.shop_owner)

        response = self.client.post(
            f"/api/managed-jobs/{self.managed_job.id}/files/proofs/",
            {"file": SimpleUploadedFile("proof.pdf", b"proof-bytes", content_type="application/pdf")},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertGreaterEqual(self._count(self.partner), 1)

    def test_manager_proof_approval_notifies_client_and_shop(self):
        proof = upload_proof_for_managed_job(
            managed_job=self.managed_job,
            assignment=self.assignment,
            uploaded_by=self.shop_owner,
            file=SimpleUploadedFile("proof.pdf", b"proof-bytes", content_type="application/pdf"),
        )

        manager_approve_job_proof(job_file=proof, actor=self.partner, notes="Looks good.")

        self.assertGreaterEqual(self._count(self.client_user), 1)
        self.assertGreaterEqual(self._count(self.shop_owner), 1)

    def test_client_proof_approval_notifies_broker_and_shop(self):
        proof = upload_proof_for_managed_job(
            managed_job=self.managed_job,
            assignment=self.assignment,
            uploaded_by=self.shop_owner,
            file=SimpleUploadedFile("proof.pdf", b"proof-bytes", content_type="application/pdf"),
        )
        manager_approve_job_proof(job_file=proof, actor=self.partner)
        self.client.force_authenticate(user=self.client_user)

        response = self.client.post(
            f"/api/job-files/{proof.id}/approve/",
            {"note": "Approved client side."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(self._count(self.partner), 1)
        self.assertGreaterEqual(self._count(self.shop_owner), 1)

    def test_revision_request_notifies_broker_and_shop(self):
        proof = upload_proof_for_managed_job(
            managed_job=self.managed_job,
            assignment=self.assignment,
            uploaded_by=self.shop_owner,
            file=SimpleUploadedFile("proof.pdf", b"proof-bytes", content_type="application/pdf"),
        )
        manager_approve_job_proof(job_file=proof, actor=self.partner)
        self.client.force_authenticate(user=self.client_user)

        response = self.client.post(
            f"/api/job-files/{proof.id}/request-revision/",
            {"note": "Fix the logo colour."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(self._count(self.partner), 1)
        self.assertGreaterEqual(self._count(self.shop_owner), 1)
        proof.refresh_from_db()
        self.assertEqual(proof.status, "revision_requested")