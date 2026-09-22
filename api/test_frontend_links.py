"""Tests for legacy deep-link normalisation."""
from django.test import SimpleTestCase

from api.frontend_links import normalize_frontend_path


class NormalizeFrontendPathTestCase(SimpleTestCase):
    def test_legacy_client_quote_deep_link_maps_to_buyer_quotes_tab(self):
        self.assertEqual(
            normalize_frontend_path("/dashboard/client/requests/2/quote/1"),
            "/app/buyer?tab=quote",
        )

    def test_legacy_client_request_link_maps_to_buyer_quotes_tab(self):
        self.assertEqual(
            normalize_frontend_path("/dashboard/client/requests/2"),
            "/app/buyer?tab=quote",
        )

    def test_legacy_client_quotes_and_jobs_map_to_buyer(self):
        self.assertEqual(
            normalize_frontend_path("/dashboard/client/quotes/2"),
            "/app/buyer?tab=quote",
        )
        self.assertEqual(
            normalize_frontend_path("/dashboard/client/jobs/9"),
            "/app/buyer",
        )
        self.assertEqual(
            normalize_frontend_path("/dashboard/client"),
            "/app/buyer",
        )

    def test_legacy_partner_shop_and_admin_routes_map_by_role(self):
        self.assertEqual(
            normalize_frontend_path("/dashboard/partner/quotes/2"),
            "/app/manager",
        )
        self.assertEqual(
            normalize_frontend_path("/dashboard/production/jobs/3"),
            "/app/printer",
        )
        self.assertEqual(
            normalize_frontend_path("/dashboard/shop/requests/2/quote/1"),
            "/app/printer",
        )
        self.assertEqual(
            normalize_frontend_path("/dashboard/admin"),
            "/app/admin",
        )

    def test_absolute_urls_are_normalised_then_mapped(self):
        self.assertEqual(
            normalize_frontend_path("https://printy.ke/dashboard/client/requests/2/quote/1"),
            "/app/buyer?tab=quote",
        )

    def test_routed_links_pass_through_unchanged(self):
        for routed in [
            "/app/buyer?tab=quote",
            "/app/buyer",
            "/app/manager",
            "/app/printer",
            "/app/admin",
        ]:
            self.assertEqual(normalize_frontend_path(routed), routed)

    def test_unrelated_paths_pass_through_unchanged(self):
        self.assertEqual(normalize_frontend_path("/quotes"), "/quotes")
        self.assertEqual(normalize_frontend_path(""), "")

    def test_unknown_dashboard_path_falls_back_to_home(self):
        self.assertEqual(normalize_frontend_path("/dashboard/something/else"), "/")