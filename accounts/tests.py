from django.test import TestCase
from rest_framework.test import APIClient

from .models import User, UserProfile


class AccountProfileAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="owner@test.com",
            password="pass12345",
            name="Owner User",
        )
        self.client.force_authenticate(user=self.user)

    def test_users_me_updates_user_and_profile_fields(self):
        response = self.client.patch(
            "/api/users/me/",
            {
                "first_name": "Amina",
                "last_name": "Otieno",
                "role": "PRINTER",
                "preferred_language": "sw",
                "phone": "+254700000000",
                "bio": "Print production lead",
                "address": "Muthithi Road",
                "city": "Westlands",
                "state": "Nairobi",
                "country": "Kenya",
                "postal_code": "00100",
                "social_links": [
                    {"platform": "website", "url": "https://printy.ke"},
                    {"platform": "instagram", "url": "https://instagram.com/printy"},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        profile = UserProfile.objects.get(user=self.user)

        self.assertEqual(self.user.first_name, "Amina")
        self.assertEqual(self.user.last_name, "Otieno")
        self.assertEqual(self.user.name, "Amina Otieno")
        self.assertEqual(self.user.role, "PRINTER")
        self.assertEqual(self.user.preferred_language, "sw")
        self.assertEqual(profile.phone, "+254700000000")
        self.assertEqual(profile.city, "Westlands")
        self.assertEqual(profile.social_links.count(), 2)
        self.assertEqual(response.json()["social_links"][0]["platform"], "website")

    def test_profiles_me_patch_persists_nested_social_links(self):
        response = self.client.patch(
            "/api/profiles/me/",
            {
                "bio": "Offset and digital specialist",
                "phone": "+254711111111",
                "address": "Madonna House, 2nd Floor",
                "city": "Westlands",
                "state": "Nairobi",
                "country": "Kenya",
                "postal_code": "00800",
                "social_links": [
                    {"platform": "linkedin", "url": "https://linkedin.com/in/printy"},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.bio, "Offset and digital specialist")
        self.assertEqual(profile.social_links.count(), 1)
        self.assertEqual(response.json()["phone"], "+254711111111")

    def test_profile_social_link_routes_allow_create_and_delete(self):
        profile = UserProfile.objects.create(user=self.user)

        create_response = self.client.post(
            f"/api/profiles/{profile.id}/social-links/",
            {"platform": "facebook", "url": "https://facebook.com/printy"},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)

        link_id = create_response.json()["id"]
        delete_response = self.client.delete(f"/api/social-links/{link_id}/")
        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(profile.social_links.count(), 0)
