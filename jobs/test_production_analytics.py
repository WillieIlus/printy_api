"""Tests for internal production analytics (law 1.3 / 3.4 / 10.3).

Covers: unknown actuals stay unknown (never zeroed), zero is a real recorded
measurement, estimate and actual remain distinct, every aggregate reports the
records behind it, weak samples are hidden rather than presented as reliable,
and the endpoint is only reachable by authorized manager/admin accounts.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from jobs.analytics import (
    estimate_vs_actual,
    quote_acceptance,
    reprints,
    turnaround,
    waste,
)
from jobs.completion_actuals import record_completion_actuals
from jobs.models import ManagedJob
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import QuoteStatus
from quotes.models import Quote, QuoteRequest, QuoteVersion, QuoteVersionOutcome
from shops.models import Shop


class ProductionAnalyticsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
        self.shop = Shop.objects.create(name="Analytics Shop", slug="analytics-shop", owner=self.owner)
        self.client_user = User.objects.create_user(email="client@example.com", password="pw12345678")
        self.manager = User.objects.create_user(
            email="manager@example.com",
            password="pw12345678",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.other_manager = User.objects.create_user(
            email="manager2@example.com",
            password="pw12345678",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.admin = User.objects.create_user(email="admin@example.com", password="pw12345678", is_staff=True)
        self.api = APIClient()

    # --- helpers -------------------------------------------------------
    def _make_job(
        self,
        *,
        broker=None,
        production_cost="700.00",
        actual_cost=None,
        accepted_at=None,
        delivered=None,
        completed=None,
        **actuals,
    ):
        request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            customer_name="Client",
            status=QuoteStatus.CLOSED,
        )
        quote = Quote.objects.create(
            quote_request=request,
            shop=self.shop,
            created_by=self.owner,
            status="accepted",
            total=Decimal("1000.00"),
        )
        create_quote_financial_split(
            quote=quote,
            production_cost=Decimal(production_cost),
            manager_markup=Decimal("300.00"),
        )
        job = ManagedJob.objects.create(
            source_quote=quote,
            source_quote_request=request,
            title="Job",
            client_total=Decimal("1000.00"),
            broker=broker,
            accepted_at=accepted_at,
            delivered_at=delivered,
            completed_at=completed,
        )
        if actual_cost is not None or actuals:
            record_completion_actuals(
                managed_job=job,
                recorded_by=self.owner,
                actual_production_cost=actual_cost,
                **actuals,
            )
        return job

    # --- unknown vs zero ------------------------------------------------
    def test_weak_sample_is_hidden_not_fabricated(self):
        self._make_job(actual_cost=Decimal("800.00"))
        metric = estimate_vs_actual()
        self.assertEqual(metric["sample_size"], 1)
        self.assertFalse(metric["sufficient"])
        self.assertIsNone(metric["value"])

    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=2)
    def test_variance_aggregate_when_sample_sufficient(self):
        self._make_job(actual_cost=Decimal("820.00"))
        self._make_job(actual_cost=Decimal("780.00"))
        metric = estimate_vs_actual()
        self.assertTrue(metric["sufficient"])
        self.assertEqual(metric["value"]["total_estimate"], "1400.00")
        self.assertEqual(metric["value"]["total_actual"], "1600.00")
        self.assertEqual(metric["value"]["variance"], "200.00")
        self.assertEqual(metric["value"]["variance_pct"], "14.29")

    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_missing_actual_is_excluded_not_zerod(self):
        self._make_job(spoilage_sheets=3)
        metric = estimate_vs_actual()
        self.assertEqual(metric["sample_size"], 0)
        self.assertIsNone(metric["value"])

        waste_metric = waste()
        self.assertEqual(waste_metric["sample_size"], 1)
        self.assertEqual(waste_metric["value"]["total_waste_sheets"], 3)

    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_zero_actual_is_a_real_measurement(self):
        self._make_job(actual_cost=Decimal("0.00"))
        metric = estimate_vs_actual()
        self.assertEqual(metric["sample_size"], 1)
        self.assertEqual(metric["value"]["total_actual"], "0.00")
        self.assertEqual(metric["value"]["variance"], "-700.00")

    # --- turnaround -----------------------------------------------------
    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_turnaround_from_real_timestamps(self):
        start = timezone.now() - timedelta(hours=48)
        self._make_job(accepted_at=start, delivered=timezone.now())
        self._make_job(accepted_at=timezone.now())
        metric = turnaround()
        self.assertEqual(metric["sample_size"], 1)
        self.assertAlmostEqual(metric["value"]["average_hours"], 48.0, places=1)

    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_recorded_turnaround_is_preferred(self):
        self._make_job(actual_cost=Decimal("700.00"), turnaround_hours=10)
        metric = turnaround()
        self.assertEqual(metric["sample_size"], 1)
        self.assertEqual(metric["value"]["average_hours"], 10.0)

    # --- reprints / waste ----------------------------------------------
    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_reprints_total(self):
        self._make_job(actual_cost=Decimal("700.00"), reprints=2)
        self._make_job(actual_cost=Decimal("700.00"), reprints=1)
        metric = reprints()
        self.assertEqual(metric["sample_size"], 2)
        self.assertEqual(metric["value"]["total_reprints"], 3)

    # --- quote acceptance ----------------------------------------------
    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_quote_acceptance_excludes_open_and_superseded(self):
        for outcome in (
            QuoteVersionOutcome.ACCEPTED,
            QuoteVersionOutcome.LOST,
            QuoteVersionOutcome.EXPIRED,
            QuoteVersionOutcome.OPEN,
            QuoteVersionOutcome.SUPERSEDED,
        ):
            request = QuoteRequest.objects.create(
                shop=self.shop,
                created_by=self.client_user,
                customer_name="Client",
                status=QuoteStatus.QUOTED,
            )
            QuoteVersion.objects.create(
                quote_request=request,
                version_number=1,
                outcome=outcome,
                total=Decimal("100.00"),
            )
        metric = quote_acceptance(QuoteVersion.objects.all())
        self.assertEqual(metric["sample_size"], 3)
        self.assertEqual(metric["value"]["accepted"], 1)
        self.assertEqual(metric["value"]["decided"], 3)
        self.assertAlmostEqual(metric["value"]["rate"], 0.3333, places=4)

    # --- access control -------------------------------------------------
    def test_access_is_restricted_to_manager_and_admin(self):
        url = reverse("dashboard-internal-production-analytics")
        for user in (self.client_user, self.owner):
            self.api.force_authenticate(user=user)
            self.assertEqual(self.api.get(url).status_code, 403)

        self.api.force_authenticate(user=None)
        self.assertIn(self.api.get(url).status_code, (401, 403))

        self.api.force_authenticate(user=self.manager)
        self.assertEqual(self.api.get(url).status_code, 200)

        self.api.force_authenticate(user=self.admin)
        self.assertEqual(self.api.get(url).status_code, 200)

    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_manager_only_sees_own_jobs(self):
        self._make_job(broker=self.manager, actual_cost=Decimal("800.00"))
        self._make_job(broker=self.other_manager, actual_cost=Decimal("900.00"))
        self.api.force_authenticate(user=self.manager)
        response = self.api.get(reverse("dashboard-internal-production-analytics"))
        self.assertEqual(response.data["scope"], "manager")
        metric = response.data["metrics"]["estimate_vs_actual"]
        self.assertEqual(metric["sample_size"], 1)
        self.assertEqual(metric["value"]["total_actual"], "800.00")

    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_admin_sees_all_jobs(self):
        self._make_job(broker=self.manager, actual_cost=Decimal("800.00"))
        self._make_job(broker=self.other_manager, actual_cost=Decimal("900.00"))
        self.api.force_authenticate(user=self.admin)
        response = self.api.get(reverse("dashboard-internal-production-analytics"))
        self.assertEqual(response.data["scope"], "admin")
        metric = response.data["metrics"]["estimate_vs_actual"]
        self.assertEqual(metric["sample_size"], 2)
        self.assertEqual(metric["value"]["total_actual"], "1700.00")

    # --- capture endpoint ----------------------------------------------
    def test_capture_endpoint_keeps_unknown_as_null(self):
        job = self._make_job(broker=self.manager)
        self.api.force_authenticate(user=self.manager)
        url = reverse(
            "dashboard-internal-job-completion-actuals", kwargs={"job_id": job.id}
        )
        response = self.api.post(
            url,
            {"actual_production_cost": "820.00", "spoilage_sheets": 4},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        metadata = response.data["metadata"]
        self.assertEqual(metadata["actual_production_cost"], "820.00")
        self.assertEqual(metadata["spoilage_sheets"], 4)
        self.assertIsNone(metadata["material_cost"])
        self.assertIsNone(metadata["material_quantity"])
        self.assertEqual(metadata["estimate"]["production_cost"], "700.00")

    def test_capture_endpoint_accepts_zero_as_real(self):
        job = self._make_job(broker=self.manager)
        self.api.force_authenticate(user=self.manager)
        url = reverse(
            "dashboard-internal-job-completion-actuals", kwargs={"job_id": job.id}
        )
        response = self.api.post(url, {"actual_production_cost": "0.00"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["metadata"]["actual_production_cost"], "0.00")

    def test_capture_endpoint_refuses_no_real_figures(self):
        job = self._make_job(broker=self.manager)
        self.api.force_authenticate(user=self.manager)
        url = reverse(
            "dashboard-internal-job-completion-actuals", kwargs={"job_id": job.id}
        )
        self.assertEqual(self.api.post(url, {}, format="json").status_code, 400)

    def test_capture_endpoint_scopes_and_denies(self):
        own = self._make_job(broker=self.manager)
        other = self._make_job(broker=self.other_manager)
        self.api.force_authenticate(user=self.manager)
        own_url = reverse(
            "dashboard-internal-job-completion-actuals", kwargs={"job_id": own.id}
        )
        other_url = reverse(
            "dashboard-internal-job-completion-actuals", kwargs={"job_id": other.id}
        )
        self.assertEqual(
            self.api.post(own_url, {"billable_sheets": 100}, format="json").status_code,
            201,
        )
        self.assertEqual(
            self.api.post(
                other_url, {"billable_sheets": 100}, format="json"
            ).status_code,
            404,
        )
        for user in (self.client_user, self.owner):
            self.api.force_authenticate(user=user)
            self.assertEqual(
                self.api.post(
                    own_url, {"billable_sheets": 1}, format="json"
                ).status_code,
                403,
            )

