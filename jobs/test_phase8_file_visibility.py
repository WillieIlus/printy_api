from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from jobs.file_services import import_legacy_files_to_managed_job
from jobs.models import JobFile, ManagedJob
from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent, QuoteOfferStatus, QuoteStatus
from quotes.models import CalculatorDraft, PendingArtworkUpload, Quote, QuoteRequest
from quotes.pending_artwork import claim_pending_artwork_to_quote_request, create_pending_artwork_upload
from quotes.services_workflow import send_calculator_draft_to_shops
from shops.models import Shop


class Phase8PendingArtworkVisibilityTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client_user = User.objects.create_user(email="phase8-client@test.com", password="pass12345", role="client")
        self.manager = User.objects.create_user(email="phase8-manager@test.com", password="pass12345", role="broker")
        self.other_manager = User.objects.create_user(email="phase8-other-manager@test.com", password="pass12345", role="broker")
        self.admin_user = User.objects.create_user(email="phase8-admin@test.com", password="pass12345", is_staff=True)
        self.shop_owner = User.objects.create_user(email="phase8-shop@test.com", password="pass12345", role="shop_owner")
        self.other_owner = User.objects.create_user(email="phase8-other@test.com", password="pass12345", role="shop_owner")
        self.shop = Shop.objects.create(owner=self.shop_owner, name="Phase 8 Shop", slug="phase-8-shop", is_active=True)
        self.other_shop = Shop.objects.create(owner=self.other_owner, name="Other Phase 8 Shop", slug="phase-8-other", is_active=True)

    def _quote_request(self, *, shop):
        return QuoteRequest.objects.create(
            shop=shop,
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Phase 8 Client",
            customer_email=self.client_user.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot={"visibility": {"topology_mode": "managed", "actor": "client"}},
        )

    def test_pending_artwork_can_attach_to_multiple_quote_requests_before_cleanup(self):
        upload = create_pending_artwork_upload(
            uploaded_file=SimpleUploadedFile("design.pdf", b"phase8 artwork", content_type="application/pdf"),
            session_key="phase8-session",
        )
        first_request = self._quote_request(shop=self.shop)
        second_request = self._quote_request(shop=self.other_shop)

        first_attachment = claim_pending_artwork_to_quote_request(
            token=upload.token,
            quote_request=first_request,
            claimed_by=self.client_user,
            delete_after_claim=False,
        )
        second_attachment = claim_pending_artwork_to_quote_request(
            token=upload.token,
            quote_request=second_request,
            claimed_by=self.client_user,
            delete_after_claim=True,
        )

        self.assertEqual(first_attachment.name, "design.pdf")
        self.assertEqual(second_attachment.name, "design.pdf")
        self.assertEqual(first_request.attachments.count(), 1)
        self.assertEqual(second_request.attachments.count(), 1)
        self.assertFalse(PendingArtworkUpload.objects.filter(token=upload.token).exists())

    def test_multi_shop_calculator_send_copies_uploaded_artwork_to_each_request(self):
        upload = create_pending_artwork_upload(
            uploaded_file=SimpleUploadedFile("multi-design.pdf", b"phase8 multi artwork", content_type="application/pdf"),
            session_key="phase8-session",
        )
        draft = CalculatorDraft.objects.create(
            user=self.manager,
            title="Multi-shop artwork quote",
            calculator_inputs_snapshot={"product_type": "flyer", "quantity": 500},
            request_details_snapshot={"client_id": self.client_user.id, "customer_name": "Phase 8 Client"},
            artwork_token=upload.token,
            artwork_filename="multi-design.pdf",
            calculator_context=CalculatorDraftContext.MANAGER_DASHBOARD,
            intent=CalculatorDraftIntent.SOURCE_PRODUCTION,
        )

        quote_requests = send_calculator_draft_to_shops(
            draft=draft,
            shops=[self.shop, self.other_shop],
            caller=self.manager,
        )

        self.assertEqual(len(quote_requests), 2)
        self.assertEqual([request.attachments.count() for request in quote_requests], [1, 1])
        self.assertEqual(
            [request.attachments.first().name for request in quote_requests],
            ["multi-design.pdf", "multi-design.pdf"],
        )
        self.assertFalse(PendingArtworkUpload.objects.filter(token=upload.token).exists())

    def test_quote_request_attachment_uses_authenticated_download_url(self):
        quote_request = self._quote_request(shop=self.shop)
        attachment = quote_request.attachments.create(
            file=SimpleUploadedFile("quote-artwork.pdf", b"quote artwork", content_type="application/pdf"),
            name="quote-artwork.pdf",
        )

        self.client.force_authenticate(user=self.manager)
        detail_response = self.client.get(f"/api/dashboard/partner/quotes/{quote_request.id}/")
        self.assertEqual(detail_response.status_code, 200)
        attachments = detail_response.json()["quote"]["attachments"]
        self.assertEqual(attachments[0]["name"], "quote-artwork.pdf")
        self.assertIn("/api/quote-request-attachments/", attachments[0]["download_url"])
        self.assertNotIn("file", attachments[0])

        download_response = self.client.get(attachments[0]["download_url"])
        self.assertEqual(download_response.status_code, 200)

        self.client.force_authenticate(user=self.client_user)
        client_download_response = self.client.get(f"/api/quote-request-attachments/{attachment.id}/download/")
        self.assertEqual(client_download_response.status_code, 200)

        self.client.force_authenticate(user=self.admin_user)
        admin_download_response = self.client.get(f"/api/quote-request-attachments/{attachment.id}/download/")
        self.assertEqual(admin_download_response.status_code, 200)

        self.client.force_authenticate(user=self.other_manager)
        unrelated_detail_response = self.client.get(f"/api/dashboard/partner/quotes/{quote_request.id}/")
        self.assertEqual(unrelated_detail_response.status_code, 404)
        unrelated_download_response = self.client.get(f"/api/quote-request-attachments/{attachment.id}/download/")
        self.assertEqual(unrelated_download_response.status_code, 403)

    def test_imported_uploaded_artwork_is_visible_to_manager_and_assigned_shop_only(self):
        quote_request = self._quote_request(shop=self.shop)
        quote_request.attachments.create(
            file=SimpleUploadedFile("customer-artwork.pdf", b"customer artwork", content_type="application/pdf"),
            name="customer-artwork.pdf",
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.shop_owner,
            status=QuoteOfferStatus.ACCEPTED,
            total=Decimal("1200.00"),
        )
        managed_job = ManagedJob.objects.create(
            title="Phase 8 managed job",
            source_quote_request=quote_request,
            source_quote=quote,
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status="assigned",
            payment_status="confirmed",
        )

        import_legacy_files_to_managed_job(managed_job=managed_job, quote_request=quote_request, quote=quote)
        job_file = JobFile.objects.get(managed_job=managed_job, original_filename="customer-artwork.pdf")

        self.client.force_authenticate(user=self.manager)
        manager_response = self.client.get(f"/api/managed-jobs/{managed_job.id}/files/")
        self.assertEqual(manager_response.status_code, 200)
        self.assertEqual([item["original_filename"] for item in manager_response.json()], ["customer-artwork.pdf"])
        manager_download_response = self.client.get(f"/api/job-files/{job_file.id}/download/")
        self.assertEqual(manager_download_response.status_code, 200)

        self.client.force_authenticate(user=self.client_user)
        client_response = self.client.get(f"/api/managed-jobs/{managed_job.id}/files/")
        self.assertEqual(client_response.status_code, 200)
        self.assertEqual([item["original_filename"] for item in client_response.json()], ["customer-artwork.pdf"])
        client_download_response = self.client.get(f"/api/job-files/{job_file.id}/download/")
        self.assertEqual(client_download_response.status_code, 200)

        self.client.force_authenticate(user=self.admin_user)
        admin_response = self.client.get(f"/api/managed-jobs/{managed_job.id}/files/")
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual([item["original_filename"] for item in admin_response.json()], ["customer-artwork.pdf"])

        self.client.force_authenticate(user=self.shop_owner)
        shop_response = self.client.get(f"/api/managed-jobs/{managed_job.id}/files/")
        self.assertEqual(shop_response.status_code, 200)
        self.assertEqual([item["original_filename"] for item in shop_response.json()], ["customer-artwork.pdf"])
        download_response = self.client.get(f"/api/job-files/{job_file.id}/download/")
        self.assertEqual(download_response.status_code, 200)

        self.client.force_authenticate(user=self.other_owner)
        other_response = self.client.get(f"/api/managed-jobs/{managed_job.id}/files/")
        self.assertEqual(other_response.status_code, 403)
        other_download_response = self.client.get(f"/api/job-files/{job_file.id}/download/")
        self.assertEqual(other_download_response.status_code, 403)

        self.client.force_authenticate(user=self.other_manager)
        other_manager_response = self.client.get(f"/api/managed-jobs/{managed_job.id}/files/")
        self.assertEqual(other_manager_response.status_code, 403)
        other_manager_download_response = self.client.get(f"/api/job-files/{job_file.id}/download/")
        self.assertEqual(other_manager_download_response.status_code, 403)

        self.client.force_authenticate(user=None)
        tracking_response = self.client.get(f"/api/public/managed-jobs/track/{managed_job.tracking_token}/")
        self.assertEqual(tracking_response.status_code, 200)
        tracking_payload = tracking_response.json()
        self.assertNotIn("files", tracking_payload)
        self.assertNotIn("attachments", tracking_payload)
        self.assertFalse(any("download_url" in str(value) or "quote-request-attachments" in str(value) for value in tracking_payload.values()))
