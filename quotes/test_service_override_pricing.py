"""Regression guard for the deprecated service models.

``QuoteItemService`` and ``QuoteRequestService`` are transitional models that
carry no rate-card FK (see ``quotes/models.py``).  Historically the pricing
helpers still referenced a non-existent ``service_rate`` field, which raised
``FieldError`` for *every* sheet item and made quote pricing / previews crash.

A service now contributes to the total only through an explicit seller
``price_override``.  These tests pin that behaviour and guard against the
``service_rate`` reference regressing back in.
"""

from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from quotes.models import QuoteItem, QuoteItemService, QuoteRequest
from quotes.pricing_service import _compute_services_total
from quotes.services import _get_service_price
from shops.models import Shop


class ServiceOverridePricingTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(email="svc@test.com", password="pass12345")
        shop = Shop.objects.create(
            owner=owner,
            name="Service Shop",
            slug="service-shop",
            is_active=True,
            currency="KES",
        )
        request = QuoteRequest.objects.create(
            shop=shop,
            created_by=owner,
            customer_name="Client",
            status="DRAFT",
        )
        self.item = QuoteItem.objects.create(
            quote_request=request,
            item_type="CUSTOM",
            title="Custom",
            quantity=1,
        )

    def test_selected_overrides_are_summed(self):
        QuoteItemService.objects.create(
            quote_item=self.item, is_selected=True, price_override=Decimal("150.00")
        )
        QuoteItemService.objects.create(
            quote_item=self.item, is_selected=True, price_override=Decimal("50.00")
        )
        self.assertEqual(_compute_services_total(self.item), Decimal("200.00"))

    def test_unselected_and_null_override_are_ignored(self):
        QuoteItemService.objects.create(
            quote_item=self.item, is_selected=False, price_override=Decimal("999.00")
        )
        QuoteItemService.objects.create(
            quote_item=self.item, is_selected=True, price_override=None
        )
        self.assertEqual(_compute_services_total(self.item), Decimal("0"))

    def test_no_services_is_zero(self):
        self.assertEqual(_compute_services_total(self.item), Decimal("0"))

    def test_get_service_price_requires_override(self):
        self.assertIsNone(_get_service_price(None))
        self.assertEqual(_get_service_price(Decimal("75.00")), Decimal("75.00"))

    def test_helpers_never_reference_service_rate(self):
        import quotes.pricing_service as pricing_service
        import quotes.services as services

        for module in (pricing_service, services):
            source = open(module.__file__, encoding="utf-8").read()
            self.assertNotIn("service_rate", source)
