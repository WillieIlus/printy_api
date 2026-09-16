from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from accounts.models import User
from inventory.choices import MachineType, PaperCategory, PaperType, SheetSize
from inventory.models import Machine, Paper
from jobs.models import JobAssignment, ManagedJob
from pricing.models import FinishingRate, PrintingRate
from shops.models import Shop


class ProductionDashboardStateTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.no_shop_user = User.objects.create_user(
            email="phase6-no-shop@test.com",
            password="pass12345",
            role="production",
        )
        self.incomplete_user = User.objects.create_user(
            email="phase6-incomplete@test.com",
            password="pass12345",
            role="production",
        )
        Shop.objects.create(
            owner=self.incomplete_user,
            name="Phase 6 Incomplete Shop",
            slug="phase6-incomplete-shop",
            is_active=True,
        )
        self.active_user = User.objects.create_user(
            email="phase6-active@test.com",
            password="pass12345",
            role="production",
        )
        self.active_shop = self._create_active_shop(self.active_user)
        self.active_job = ManagedJob.objects.create(
            title="Phase 6 production job",
            client=self.active_user,
            assigned_shop=self.active_shop,
            created_by=self.active_user,
            status="in_production",
            payment_status="confirmed",
        )
        JobAssignment.objects.create(
            managed_job=self.active_job,
            assigned_shop=self.active_shop,
            status="accepted",
            shop_payout=Decimal("1200.00"),
        )

    def _create_active_shop(self, owner):
        shop = Shop.objects.create(
            owner=owner,
            name="Phase 6 Active Shop",
            slug="phase6-active-shop",
            is_active=True,
            city="Nairobi",
        )
        machine = Machine.objects.create(
            shop=shop,
            name="Digital Press",
            machine_type=MachineType.DIGITAL,
            max_width_mm=450,
            max_height_mm=320,
            is_active=True,
        )
        Paper.objects.create(
            shop=shop,
            name="300gsm Matte",
            sheet_size=SheetSize.SRA3,
            gsm=300,
            paper_type=PaperType.MATTE,
            category=PaperCategory.ARTCARD,
            buying_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            quantity_in_stock=500,
            is_active=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size=SheetSize.SRA3,
            color_mode="COLOR",
            single_price=Decimal("15.00"),
            double_price=Decimal("30.00"),
            is_active=True,
            is_default=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug="phase6-cutting",
            price=Decimal("50.00"),
            is_active=True,
        )
        return shop

    def test_production_dashboard_routes_render_for_setup_states(self):
        routes = [
            "/api/dashboard/production/jobs/",
            "/api/shop/assignments/",
            "/api/dashboard/production/paper-stock/",
            "/api/dashboard/production/pricing/",
            "/api/dashboard/production/finishings/",
            "/api/dashboard/production/payments/",
            "/api/shop/messages/",
        ]
        states = [
            (self.no_shop_user, 0),
            (self.incomplete_user, 0),
            (self.active_user, 1),
        ]

        for user, expected_active_count in states:
            self.client.force_authenticate(user=user)
            for route in routes:
                with self.subTest(user=user.email, route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 200)
            jobs_response = self.client.get("/api/dashboard/production/jobs/")
            self.assertEqual(len(jobs_response.json()["results"]), expected_active_count)

        self.client.force_authenticate(user=self.active_user)
        response = self.client.get(f"/api/dashboard/printshop/jobs/{self.active_job.id}/breakdown/")
        self.assertEqual(response.status_code, 200)
