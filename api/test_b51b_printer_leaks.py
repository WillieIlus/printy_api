from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from api.test_actor_visibility import ActorVisibilityTestCase
from quotes.models import Quote, QuoteRequest
from shops.models import Shop
from workflow.models import WorkflowJob, WorkflowManager


WORKFLOW_ACTIONS = (
    "approve",
    "request-changes",
    "pay",
    "assign",
    "press-advance",
    "confirm-delivery",
    "resolve-dispute",
    "nudge",
)


class WorkflowJobsAccessTest(TestCase):
    def setUp(self):
        self.manager_record = WorkflowManager.objects.create(
            id="m-leak",
            name="Leak Manager",
            initials="LM",
        )
        self.job = WorkflowJob.objects.create(
            id="j-leak",
            code="PTY-LEAK",
            title="Private buyer job",
            product="A5 flyer",
            qty=100,
            value=Decimal("999.00"),
            buyer_id="buyer-private",
            buyer_name="Private Buyer",
            buyer_company="Private Co",
            manager=self.manager_record,
            specs={"material": "130gsm gloss"},
            owner={"name": "Private Buyer", "role": "Buyer", "action": "Approve", "waitingHrs": 0, "slaHrs": 24},
            placed_at="Today",
            history=[],
            feed=[],
        )
        self.ops = User.objects.create_user(
            email="b51b-ops@example.com",
            password="x",
            is_staff=True,
        )
        self.printer = User.objects.create_user(
            email="b51b-printer@example.com",
            password="x",
            role=User.Role.PRODUCTION,
        )
        self.buyer = User.objects.create_user(
            email="b51b-buyer@example.com",
            password="x",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="b51b-manager@example.com",
            password="x",
            role=User.Role.PARTNER,
        )

    def _client(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def test_anonymous_requests_are_denied(self):
        client = self._client()
        requests = [
            ("get", "/api/workflow/jobs/"),
            ("post", "/api/workflow/jobs/"),
            ("get", "/api/workflow/jobs/j-leak/"),
            ("put", "/api/workflow/jobs/j-leak/"),
            ("patch", "/api/workflow/jobs/j-leak/"),
            ("delete", "/api/workflow/jobs/j-leak/"),
        ]
        requests.extend(
            ("post", f"/api/workflow/jobs/j-leak/{action}/") for action in WORKFLOW_ACTIONS
        )
        for method, path in requests:
            response = getattr(client, method)(path, {} if method != "get" else None, format="json")
            self.assertIn(
                response.status_code,
                (401, 403),
                f"anonymous {method.upper()} {path} returned {response.status_code}",
            )

    def test_non_ops_roles_cannot_read_or_transition(self):
        for user in (self.printer, self.buyer, self.manager):
            client = self._client(user)
            self.assertIn(
                client.get("/api/workflow/jobs/").status_code,
                (401, 403),
                f"{user.role} listed workflow jobs",
            )
            self.assertIn(
                client.get("/api/workflow/jobs/j-leak/").status_code,
                (401, 403),
                f"{user.role} read a workflow job detail",
            )
            self.assertIn(
                client.post("/api/workflow/jobs/j-leak/nudge/", {}, format="json").status_code,
                (401, 403),
                f"{user.role} ran a workflow transition",
            )

    def test_ops_keep_full_read_shape_and_can_transition(self):
        client = self._client(self.ops)

        list_response = client.get("/api/workflow/jobs/")
        self.assertEqual(list_response.status_code, 200)
        row = list_response.json()[0]
        for key in ("value", "buyerId", "buyerName", "buyerCompany"):
            self.assertIn(key, row)
        self.assertEqual(row["value"], 999.0)
        self.assertEqual(row["buyerName"], "Private Buyer")

        detail_response = client.get("/api/workflow/jobs/j-leak/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["buyerCompany"], "Private Co")

        transition = client.post("/api/workflow/jobs/j-leak/nudge/", {}, format="json")
        self.assertEqual(transition.status_code, 200)


class ShopHomeDashboardPrivacyTest(TestCase):
    def setUp(self):
        self.shop_owner = User.objects.create_user(
            email="b51b-shop@example.com",
            password="x",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(name="B51b Leak Shop", owner=self.shop_owner)
        self.buyer = User.objects.create_user(
            email="b51b-client@example.com",
            password="x",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="b51b-partner@example.com",
            password="x",
            role=User.Role.PARTNER,
        )
        self.ops = User.objects.create_user(
            email="b51b-shop-ops@example.com",
            password="x",
            is_staff=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            assigned_manager=self.manager,
            request_reference="REQ-B51B",
            customer_name="Private Client",
            customer_email="private-client@example.com",
            customer_phone="+254711111111",
            request_snapshot={
                "calculator_inputs": {"quantity": 100},
                "request_details": {"product": "Business cards", "quantity": 100},
                "internal_pricing_snapshot": {"production_cost": "1000.00"},
            },
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.shop_owner,
            status=Quote.SENT,
            total=Decimal("1500.00"),
            quote_reference="Q-B51B",
            response_snapshot={
                "customer_pricing": {"final_client_price": "1800.00"},
                "internal_pricing_snapshot": {"production_cost": "1000.00"},
            },
        )

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _assert_no_forbidden(self, payload, path="root"):
        forbidden = ActorVisibilityTestCase.SHOP_FORBIDDEN
        if isinstance(payload, dict):
            for key, value in payload.items():
                self.assertNotIn(key, forbidden, f"{key} leaked at {path}")
                self._assert_no_forbidden(value, f"{path}.{key}")
        elif isinstance(payload, list):
            for index, item in enumerate(payload):
                self._assert_no_forbidden(item, f"{path}[{index}]")

    def test_shop_home_hides_client_identity_and_client_total(self):
        client = self._client(self.shop_owner)
        for path in (
            "/api/dashboard/shop-home/",
            f"/api/shops/{self.shop.slug}/dashboard-home/",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            payload = response.json()
            self.assertTrue(payload["recent_requests"], path)
            self._assert_no_forbidden(payload)
            row = payload["recent_requests"][0]
            self.assertEqual(row["request_reference"], "REQ-B51B")
            self.assertIn("status", row)
            self.assertIn("latest_response", row)

    def test_ops_shop_home_keeps_full_summary(self):
        client = self._client(self.ops)
        response = client.get(f"/api/shops/{self.shop.slug}/dashboard-home/")
        self.assertEqual(response.status_code, 200)
        row = response.json()["recent_requests"][0]
        self.assertEqual(row["customer_name"], "Private Client")
        self.assertEqual(row["customer_email"], "private-client@example.com")
        self.assertEqual(row["customer_phone"], "+254711111111")
        self.assertIn("request_snapshot", row)
        self.assertEqual(Decimal(str(row["latest_response"]["total"])), Decimal("1500.00"))

    def test_partner_and_client_cannot_open_shop_home(self):
        for user in (self.manager, self.buyer):
            response = self._client(user).get(f"/api/shops/{self.shop.slug}/dashboard-home/")
            self.assertEqual(response.status_code, 403, user.role)
