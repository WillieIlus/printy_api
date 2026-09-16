from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from jobs.models import ManagedJob
from jobs.serializers import ManagedJobPublicTrackingSerializer, ManagedJobSerializer
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


User = get_user_model()


class Phase15TimingSerializerTestCase(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="phase15-client@example.com",
            password="pass12345",
            role="client",
        )
        self.shop_owner = User.objects.create_user(
            email="phase15-shop@example.com",
            password="pass12345",
            role="production",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Phase 15 Shop",
            slug="phase-15-shop",
            is_active=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            customer_name="Phase 15 Customer",
            customer_email="phase15-client@example.com",
            status=QuoteStatus.CLOSED,
        )
        self.estimated_ready_at = timezone.now() + timedelta(days=2)
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.shop_owner,
            status=QuoteOfferStatus.ACCEPTED,
            total=Decimal("2000.00"),
            estimated_ready_at=self.estimated_ready_at,
        )
        self.managed_job = ManagedJob.objects.create(
            title="Phase 15 managed job",
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status="in_production",
            client_total=Decimal("2600.00"),
            urgency_type="express",
            urgency_multiplier=Decimal("1.25"),
            urgency_fee=Decimal("150.00"),
            after_hours_fee=Decimal("0.00"),
        )

    def test_public_tracking_estimated_ready_does_not_use_actual_ready_at(self):
        actual_ready_at = timezone.now()
        self.managed_job.ready_at = actual_ready_at
        self.managed_job.save(update_fields=["ready_at"])

        payload = ManagedJobPublicTrackingSerializer(self.managed_job).data

        self.assertEqual(payload["estimated_ready"], self.estimated_ready_at)
        self.assertNotEqual(payload["estimated_ready"], actual_ready_at)

    def test_managed_job_serializer_keeps_urgency_multiplier_internal(self):
        payload = ManagedJobSerializer(self.managed_job).data

        self.assertEqual(payload["urgency_type"], "express")
        self.assertEqual(payload["urgency_fee"], "150.00")
        self.assertIn("after_hours_fee", payload)
        self.assertNotIn("urgency_multiplier", payload)