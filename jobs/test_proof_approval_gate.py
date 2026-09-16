from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from api.visibility import CLIENT_ACTOR
from jobs.choices import JobFileStatus
from jobs.file_services import (
    approve_job_proof,
    get_visible_job_files_for_actor,
    manager_approve_job_proof,
    upload_proof_for_managed_job,
)
from jobs.models import JobAssignment, JobFile, ManagedJob
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


class ManagerProofApprovalGateTestCase(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.client_user = User.objects.create_user(email="gate-client@test.com", password="pass12345", role="client")
        self.manager = User.objects.create_user(email="gate-manager@test.com", password="pass12345", role="broker")
        self.owner = User.objects.create_user(email="gate-owner@test.com", password="pass12345", role="shop_owner")
        self.shop = Shop.objects.create(owner=self.owner, name="Gate Shop", slug="gate-shop", is_active=True)
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Gate Customer",
            customer_email="gate-client@test.com",
            status=QuoteStatus.CLOSED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.owner,
            status=QuoteOfferStatus.ACCEPTED,
            total=Decimal("2500.00"),
        )
        self.managed_job = ManagedJob.objects.create(
            title="Gate job",
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status="assigned",
            payment_status="confirmed",
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.managed_job,
            assigned_shop=self.shop,
            source_quote=self.quote,
            status="accepted",
        )

    def upload_proof(self) -> JobFile:
        return upload_proof_for_managed_job(
            managed_job=self.managed_job,
            assignment=self.assignment,
            uploaded_by=self.owner,
            file=SimpleUploadedFile("proof.pdf", b"proof bytes", content_type="application/pdf"),
            original_filename="proof.pdf",
        )

    def test_uploaded_proof_waits_for_manager_and_is_hidden_from_client(self):
        proof = self.upload_proof()

        self.assertEqual(proof.status, JobFileStatus.MANAGER_REVIEW)
        client_files = list(get_visible_job_files_for_actor(managed_job=self.managed_job, actor=CLIENT_ACTOR))
        self.assertEqual(client_files, [])

        with self.assertRaisesMessage(ValueError, "manager before the client"):
            approve_job_proof(job_file=proof, actor=self.client_user)

    def test_manager_approval_releases_proof_to_client_then_client_can_approve(self):
        proof = self.upload_proof()

        manager_approve_job_proof(job_file=proof, actor=self.manager)
        proof.refresh_from_db()

        self.assertEqual(proof.status, JobFileStatus.MANAGER_APPROVED)
        client_files = list(get_visible_job_files_for_actor(managed_job=self.managed_job, actor=CLIENT_ACTOR))
        self.assertEqual([item.id for item in client_files], [proof.id])

        approve_job_proof(job_file=proof, actor=self.client_user)
        proof.refresh_from_db()
        self.assertEqual(proof.status, JobFileStatus.PROOF_APPROVED)

    def test_manager_endpoint_approves_latest_waiting_proof(self):
        proof = self.upload_proof()

        self.api_client.force_authenticate(user=self.manager)
        response = self.api_client.post(
            f"/api/dashboard/manager/jobs/{self.managed_job.id}/proof-approval/",
            {"action": "approve"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        proof.refresh_from_db()
        self.assertEqual(proof.status, JobFileStatus.MANAGER_APPROVED)
        self.assertEqual(response.json()["status"], JobFileStatus.MANAGER_APPROVED)

    def test_client_cannot_use_manager_endpoint(self):
        self.upload_proof()

        self.api_client.force_authenticate(user=self.client_user)
        response = self.api_client.post(
            f"/api/dashboard/manager/jobs/{self.managed_job.id}/proof-approval/",
            {"action": "approve"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
