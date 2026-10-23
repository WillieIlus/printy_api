"""Track lookup for entitled parties (prompt item 5).

An authenticated party on the job (buyer, manager, printer) must get the job from
``/api/track/<MJ-code>/`` with no phone/email challenge. A signed-out visitor
still needs the job number plus the phone on the job.
"""

from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import ManagedJob
from shops.models import Shop

JOB_CODE = "MJ-20261020-0022"
CUSTOMER_PHONE = "+254733444555"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AuthenticatedTrackLookupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.buyer = User.objects.create_user(
            email="track-buyer@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="track-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.shop_owner = User.objects.create_user(
            email="track-printer@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Track Print Shop",
            slug="track-print-shop",
            is_active=True,
        )
        UserProfile.objects.update_or_create(
            user=self.buyer,
            defaults={"phone": CUSTOMER_PHONE, "phone_normalized": "254733444555"},
        )
        # No source quote request: the job's own MJ reference is its code.
        self.job = ManagedJob.objects.create(
            title="Track test job",
            client=self.buyer,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.buyer,
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            client_total=Decimal("1500.00"),
        )
        self.job.managed_reference = JOB_CODE
        self.job.save(update_fields=["managed_reference", "updated_at"])

    def tearDown(self):
        cache.clear()

    def _get(self, *, user=None, **params):
        self.api.force_authenticate(user=user)
        return self.api.get(f"/api/track/{JOB_CODE}/", params)

    def test_buyer_party_gets_job_without_phone_or_email_challenge(self):
        response = self._get(user=self.buyer)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["code"], JOB_CODE)
        self.assertIn("timeline", body)
        self.assertIn("timing", body)
        self.assertNotIn("assigned_shop_name", body)

    def test_manager_party_gets_job_without_challenge(self):
        response = self._get(user=self.manager)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["code"], JOB_CODE)
        self.assertIn("timeline", body)
        self.assertEqual(body.get("assigned_shop_name"), self.shop.name)

    def test_printer_party_gets_job_without_challenge(self):
        response = self._get(user=self.shop_owner)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["code"], JOB_CODE)
        self.assertIn("timeline", body)
        # Law 7.6: the printer timeline carries no money.
        self.assertNotIn("client_total", response.content.decode())
        self.assertNotIn("1500", response.content.decode())

    def test_non_party_authenticated_user_still_needs_phone(self):
        outsider = User.objects.create_user(
            email="track-outsider@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.assertEqual(self._get(user=outsider).status_code, 404)
        matched = self._get(user=outsider, phone=CUSTOMER_PHONE)
        self.assertEqual(matched.status_code, 200, matched.content)
        self.assertEqual(set(matched.json()), {"code", "job_status", "raw_status", "dates"})

    def test_signed_out_lookup_still_requires_phone(self):
        self.assertEqual(self._get().status_code, 404)
        response = self._get(phone=CUSTOMER_PHONE)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("timeline", response.json())
