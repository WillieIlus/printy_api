"""Tests for common.geo — Haversine distance."""
from django.test import TestCase

from common.geo import haversine_km


class HaversineTestCase(TestCase):
    """Test haversine_km function."""

    def test_same_point_zero_distance(self):
        d = haversine_km(-1.29, 36.82, -1.29, 36.82)
        self.assertEqual(d, 0.0)

    def test_known_distance_nairobi_to_mombasa(self):
        # Nairobi ~ -1.29, 36.82; Mombasa ~ -4.04, 39.67; ~440 km
        d = haversine_km(-1.29, 36.82, -4.04, 39.67)
        self.assertGreater(d, 400)
        self.assertLess(d, 500)

    def test_closer_point_smaller_distance(self):
        origin = (-1.29, 36.82)
        close = (-1.30, 36.83)
        far = (-1.40, 36.90)
        d_close = haversine_km(origin[0], origin[1], close[0], close[1])
        d_far = haversine_km(origin[0], origin[1], far[0], far[1])
        self.assertLess(d_close, d_far)
