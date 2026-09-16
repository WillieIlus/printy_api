import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
from django.apps import apps

if not apps.ready:
    django.setup()

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from accounts.models import User
from production.serializers import ProductionOrderWriteSerializer
from quotes.models import QuoteRequest, Quote
from shops.models import Shop


class ProductionRelationshipFoundationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = User.objects.create_user(
            email="production-owner@test.com",
            password="pass12345",
            role=User.Role.SHOP_OWNER,
        )
        self.client_user = User.objects.create_user(
            email="production-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.shop = Shop.objects.create(name="Production Shop", slug="production-shop", owner=self.owner)
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            customer_name="Client One",
            customer_email="client-one@test.com",
            customer_phone="+254700123456",
            status=QuoteRequest.SUBMITTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.owner,
            status=Quote.ACCEPTED,
            total="2500.00",
        )

    def test_create_job_from_quote_creates_production_order(self):
        request = self.factory.post("/api/jobs/", {"quote": self.quote.id}, format="json")
        request.user = self.owner
        serializer = ProductionOrderWriteSerializer(
            data={
                "quote": self.quote.id,
                "shop": self.shop.id,
                "status": "pending",
                "quantity": 10,
            },
            context={"request": request},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        job = serializer.save()

        self.assertEqual(job.shop_id, self.shop.id)
        self.assertEqual(job.quote_id, self.quote.id)
