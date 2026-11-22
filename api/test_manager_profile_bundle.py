"""Contract tests for the guided manager profile bundle."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ManagerProfileBundleTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            email="profile-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            name="Amina Manager",
        )
        self.api = APIClient()
        self.api.force_authenticate(self.manager)

    def get_bundle(self):
        return self.api.get("/api/profiles/me/manager-profile/", HTTP_HOST="localhost")

    def test_get_returns_the_bundle_shape_and_falls_back_to_account_name(self):
        response = self.get_bundle()
        self.assertEqual(response.status_code, 200)
        payload = response.data
        self.assertIn("profile", payload)
        self.assertIn("photos", payload)
        self.assertIn("progress", payload)
        self.assertIn("publicView", payload)
        self.assertEqual(payload["profile"]["displayName"], "Amina Manager")
        self.assertEqual(payload["profile"]["approvedHeadshotId"], None)

    def test_patch_persists_public_profile_fields(self):
        response = self.api.patch(
            "/api/profiles/me/manager-profile/",
            {
                "displayName": "Amina M.",
                "bio": "Short-run specialist in Nairobi.",
                "businessName": "Sanaa Desk",
                "specialties": ["Business cards", "Booklets"],
                "areas": ["Nairobi CBD"],
                "deliveryOptions": ["Rider"],
                "languages": ["English", "Swahili"],
                "turnaroundHours": 24,
            },
            format="json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        profile = response.data["profile"]
        self.assertEqual(profile["displayName"], "Amina M.")
        self.assertEqual(profile["specialties"], ["Business cards", "Booklets"])
        self.assertEqual(profile["areas"], ["Nairobi CBD"])
        self.assertEqual(profile["deliveryOptions"], ["Rider"])
        self.assertEqual(profile["languages"], ["English", "Swahili"])
        self.assertEqual(profile["turnaroundHours"], 24)

    def test_progress_counts_real_profile_fields(self):
        response = self.get_bundle()
        self.assertGreaterEqual(response.data["progress"]["percent"], 0)
        self.assertIn("add a profile photo", response.data["progress"]["missing"])

    def test_patch_persists_public_listing_controls(self):
        response = self.api.patch(
            "/api/profiles/me/manager-profile/",
            {
                "publicOptIn": True,
                "speciality": "Same-day business cards in Nairobi CBD",
                "acceptsNewJobs": False,
                "maxConcurrentJobs": 5,
            },
            format="json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        profile = response.data["profile"]
        self.assertTrue(profile["publicOptIn"])
        self.assertEqual(
            profile["speciality"], "Same-day business cards in Nairobi CBD"
        )
        self.assertFalse(profile["acceptsNewJobs"])
        self.assertEqual(profile["maxConcurrentJobs"], 5)
        # Verification and a photo are still missing, so the profile is not public.
        self.assertFalse(profile["isPublic"])
        self.assertNotIn(
            "opt in to the public manager list", response.data["progress"]["missing"]
        )
        self.assertNotIn(
            "add a one-line public speciality", response.data["progress"]["missing"]
        )
