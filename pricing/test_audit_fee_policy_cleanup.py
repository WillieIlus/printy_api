"""Audit #6b: fee-policy fields that pricing never reads must not look live.

Six PlatformFeePolicy fields (small/medium job limits, small/medium/bulk max
multiples, add_platform_fee_on_top) are never read and their defaults disagree
with the live tiers. Seven legacy fee-rate fields only feed the pricing-rule
version hash. None of them may appear as an editable admin setting.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.admin.sites import site
from django.test import RequestFactory, TestCase

from accounts.models import User
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import calculate_quote_financials

DEPRECATED = (
    "small_job_limit",
    "medium_job_limit",
    "small_job_max_multiple",
    "medium_job_max_multiple",
    "bulk_job_max_multiple",
    "add_platform_fee_on_top",
)
LEGACY = (
    "under_production_fee_rate",
    "standard_production_fee_rate",
    "standard_markup_fee_rate",
    "high_markup_threshold",
    "high_markup_fee_rate",
    "high_production_fee_rate",
    "high_production_markup_fee_rate",
)
LIVE = (
    "under_production_threshold",
    "high_production_threshold",
    "min_markup_multiple",
    "tier_a_shop_floor_multiple",
    "tier_b_shop_floor_multiple",
    "tier_c_shop_floor_multiple",
    "tier_a_manager_commission_cap_rate",
    "tier_b_manager_commission_cap_rate",
    "tier_c_manager_commission_cap_rate",
    "tier_a_max_client_price_multiple",
    "tier_b_max_client_price_multiple",
    "tier_c_max_client_price_multiple",
    "maximum_manager_markup_multiple",
)


class FeePolicyAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(email="fee-admin@test.com", password="pass12345")
        self.model_admin = site._registry[PlatformFeePolicy]
        self.request = RequestFactory().get("/admin/pricing/platformfeepolicy/add/")
        self.request.user = self.admin_user

    def _form_fields(self):
        form_class = self.model_admin.get_form(self.request, obj=None)
        return set(form_class.base_fields)

    def test_deprecated_and_legacy_fields_are_not_editable_in_admin(self):
        fields = self._form_fields()
        for name in DEPRECATED + LEGACY:
            with self.subTest(field=name):
                self.assertNotIn(name, fields)

    def test_live_split_fields_stay_editable(self):
        fields = self._form_fields()
        for name in LIVE:
            with self.subTest(field=name):
                self.assertIn(name, fields)

    def test_deprecated_fields_say_so(self):
        for name in DEPRECATED:
            with self.subTest(field=name):
                field = PlatformFeePolicy._meta.get_field(name)
                self.assertFalse(field.editable)
                self.assertTrue(str(field.help_text).startswith("DEPRECATED"))
        for name in LEGACY:
            with self.subTest(field=name):
                self.assertTrue(str(PlatformFeePolicy._meta.get_field(name).help_text).startswith("LEGACY"))

    def test_change_page_renders_for_a_superuser(self):
        policy = PlatformFeePolicy.objects.create(name="Admin render")
        self.client.force_login(self.admin_user)
        response = self.client.get(f"/admin/pricing/platformfeepolicy/{policy.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NOT used by pricing")

    def test_list_display_shows_live_fields_only(self):
        for name in DEPRECATED + LEGACY:
            with self.subTest(field=name):
                self.assertNotIn(name, self.model_admin.list_display)

    def test_serializer_does_not_accept_writes_to_deprecated_fields(self):
        from pricing.serializers import PlatformFeePolicySerializer

        serializer = PlatformFeePolicySerializer()
        for name in DEPRECATED:
            with self.subTest(field=name):
                self.assertTrue(serializer.fields[name].read_only)


class DeprecatedFieldsAreNeverReadTests(TestCase):
    def test_changing_them_does_not_change_the_split(self):
        baseline = PlatformFeePolicy.objects.create(name="Baseline")
        changed = PlatformFeePolicy.objects.create(
            name="Changed",
            is_active=False,  # only one active policy may exist
            small_job_limit=Decimal("1.00"),
            medium_job_limit=Decimal("2.00"),
            small_job_max_multiple=Decimal("9.00"),
            medium_job_max_multiple=Decimal("9.00"),
            bulk_job_max_multiple=Decimal("9.00"),
            add_platform_fee_on_top=True,
            under_production_fee_rate=Decimal("0.9000"),
            standard_production_fee_rate=Decimal("0.9000"),
            standard_markup_fee_rate=Decimal("0.9000"),
            high_markup_threshold=Decimal("0.1000"),
            high_markup_fee_rate=Decimal("0.9000"),
            high_production_fee_rate=Decimal("0.9000"),
            high_production_markup_fee_rate=Decimal("0.9000"),
        )
        for cost, markup in ((Decimal("800"), Decimal("400")), (Decimal("5000"), Decimal("2000")), (Decimal("20000"), Decimal("5000"))):
            with self.subTest(cost=cost):
                a = calculate_quote_financials(production_cost=cost, manager_markup=markup, policy=baseline).as_dict()
                b = calculate_quote_financials(production_cost=cost, manager_markup=markup, policy=changed).as_dict()
                strip = lambda d: {k: v for k, v in d.items() if not k.startswith("policy")}  # noqa: E731
                self.assertEqual(strip(a), strip(b))
                self.assertTrue(strip(a))
