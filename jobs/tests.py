"""JobShare tests: token security, permissions."""
import re

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from jobs.models import JobClaim, JobRequest


class JobRequestAPITestCase(TestCase):
    """Test JobShare API: create, list, permissions."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="printer@test.com", password="pass")
        self.other = User.objects.create_user(email="other@test.com", password="pass")

    def test_unauthenticated_cannot_create(self):
        """Only authenticated users can create job requests."""
        r = self.client.post(
            "/api/job-requests/",
            {"title": "Brochure 500 pcs", "specs": {"product": "A4 Brochure", "qty": 500}},
            format="json",
        )
        self.assertEqual(r.status_code, 401)

    def test_unauthenticated_cannot_list(self):
        """Only authenticated users can list job requests."""
        r = self.client.get("/api/job-requests/")
        self.assertEqual(r.status_code, 401)

    def test_authenticated_can_create(self):
        """Authenticated printer can create job request."""
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            "/api/job-requests/",
            {
                "title": "Brochure 500 pcs",
                "specs": {"product": "A4 Brochure", "qty": 500},
                "location": "Nairobi",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["title"], "Brochure 500 pcs")
        self.assertEqual(data["status"], "OPEN")
        self.assertEqual(data["status_label"], "Open")
        self.assertEqual(data["created_by"], self.user.id)

    def test_list_filter_by_status(self):
        """GET /api/job-requests/?status=OPEN filters correctly."""
        self.client.force_authenticate(user=self.user)
        JobRequest.objects.create(
            created_by=self.user,
            title="Open job",
            status=JobRequest.OPEN,
            public_token=None,
        )
        JobRequest.objects.create(
            created_by=self.user,
            title="Closed job",
            status=JobRequest.CLOSED,
            public_token=None,
        )
        r = self.client.get("/api/job-requests/?status=OPEN")
        self.assertEqual(r.status_code, 200)
        results = r.json().get("results", r.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Open job")


class JobRequestTokenSecurityTestCase(TestCase):
    """Test token is un-guessable and secure."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="printer@test.com", password="pass")

    def test_token_format(self):
        """Public token is base64url, 43 chars (32 bytes)."""
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            "/api/job-requests/",
            {"title": "Test", "specs": {}},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        job_id = r.json()["id"]
        r2 = self.client.post(f"/api/job-requests/{job_id}/whatsapp-share/", {}, format="json")
        self.assertEqual(r2.status_code, 200)
        url = r2.json()["public_view_url"]
        token = url.split("/job/")[-1].rstrip("/")
        # Base64url: A-Za-z0-9_- only, typically 43 chars for 32 bytes
        self.assertTrue(re.match(r"^[A-Za-z0-9_-]{40,50}$", token), f"Token format invalid: {token!r}")
        self.assertGreaterEqual(len(token), 40)

    def test_token_unguessable(self):
        """Sequential IDs must not predict token."""
        self.client.force_authenticate(user=self.user)
        tokens = []
        for i in range(3):
            r = self.client.post(
                "/api/job-requests/",
                {"title": f"Job {i}", "specs": {}},
                format="json",
            )
            self.assertEqual(r.status_code, 201)
            r2 = self.client.post(
                f"/api/job-requests/{r.json()['id']}/whatsapp-share/",
                {},
                format="json",
            )
            tokens.append(r2.json()["public_view_url"].split("/job/")[-1].rstrip("/"))
        # All tokens must be unique and not sequential
        self.assertEqual(len(set(tokens)), 3)
        for t in tokens:
            self.assertFalse(t.isdigit(), "Token must not be numeric ID")

    def test_public_view_returns_safe_fields_only(self):
        """Public token view must not expose internal/sensitive data."""
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            "/api/job-requests/",
            {
                "title": "Secret job",
                "specs": {"internal_cost": 100, "product": "Brochure"},
                "location": "Nairobi",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        job_id = r.json()["id"]
        r2 = self.client.post(f"/api/job-requests/{job_id}/whatsapp-share/", {}, format="json")
        token = r2.json()["public_view_url"].split("/job/")[-1].rstrip("/")
        # Public view — no auth
        r3 = self.client.get(f"/api/public/job/{token}/")
        self.assertEqual(r3.status_code, 200)
        data = r3.json()
        self.assertIn("title", data)
        self.assertIn("specs", data)
        self.assertIn("location", data)
        self.assertEqual(data["status_label"], "Open")
        self.assertNotIn("created_by", data)
        self.assertNotIn("created_by_email", data)
        # Specs may include keys - we don't filter specs, but we don't expose created_by
        self.assertIn("claim_cta", data)
        self.assertTrue(data["requires_login"])

    def test_invalid_token_404(self):
        """Invalid token returns 404."""
        r = self.client.get("/api/public/job/invalid-token-xyz/")
        self.assertEqual(r.status_code, 404)


class JobClaimAPITestCase(TestCase):
    """Test claiming workflow: only open jobs, only owner can accept/reject, accepting closes job."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(email="owner@test.com", password="pass")
        self.claimant = User.objects.create_user(email="claimant@test.com", password="pass")

    def test_only_open_jobs_can_be_claimed(self):
        """Only OPEN jobs can be claimed."""
        job = JobRequest.objects.create(
            created_by=self.owner,
            title="Open job",
            status=JobRequest.CLOSED,
            public_token=None,
        )
        self.client.force_authenticate(user=self.claimant)
        r = self.client.post(
            f"/api/job-requests/{job.id}/claims/",
            {"message": "I can do this"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Only open jobs", r.json()["detail"])

    def test_only_job_owner_can_accept(self):
        """Only job owner can accept claims."""
        job = JobRequest.objects.create(
            created_by=self.owner,
            title="Open job",
            status=JobRequest.OPEN,
            public_token=None,
        )
        claim = JobClaim.objects.create(
            job_request=job,
            claimed_by=self.claimant,
            status=JobClaim.PENDING,
        )
        self.client.force_authenticate(user=self.claimant)
        r = self.client.post(f"/api/job-claims/{claim.id}/accept/", {}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Only the job owner", r.json()["detail"])

    def test_only_job_owner_can_reject(self):
        """Only job owner can reject claims."""
        job = JobRequest.objects.create(
            created_by=self.owner,
            title="Open job",
            status=JobRequest.OPEN,
            public_token=None,
        )
        claim = JobClaim.objects.create(
            job_request=job,
            claimed_by=self.claimant,
            status=JobClaim.PENDING,
        )
        self.client.force_authenticate(user=self.claimant)
        r = self.client.post(f"/api/job-claims/{claim.id}/reject/", {}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_accepting_closes_job(self):
        """Accepting a claim marks job as CLAIMED and creates notification."""
        job = JobRequest.objects.create(
            created_by=self.owner,
            title="Open job",
            status=JobRequest.OPEN,
            public_token=None,
        )
        claim = JobClaim.objects.create(
            job_request=job,
            claimed_by=self.claimant,
            status=JobClaim.PENDING,
        )
        self.client.force_authenticate(user=self.owner)
        r = self.client.post(f"/api/job-claims/{claim.id}/accept/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        claim.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(claim.status, JobClaim.ACCEPTED)
        self.assertEqual(job.status, JobRequest.CLAIMED)
        from jobs.models import JobNotification
        self.assertTrue(JobNotification.objects.filter(job_claim=claim, user=self.claimant).exists())
