from django.test import TestCase

from accounts.models import User
from shops.models import Shop


def _make_user(email="seller@test.com"):
    return User.objects.create_user(email=email, password="testpass123")


class ShopSlugTests(TestCase):
    """Verify automatic slug generation on Shop."""

    def test_slug_generated_on_create(self):
        shop = Shop.objects.create(name="Ace Printers", owner=_make_user())
        self.assertEqual(shop.slug, "ace-printers")

    def test_slug_unique_suffix(self):
        owner = _make_user()
        s1 = Shop.objects.create(name="Ace Printers", owner=owner)
        s2 = Shop.objects.create(
            name="Ace Printers", owner=_make_user("seller2@test.com")
        )
        self.assertEqual(s1.slug, "ace-printers")
        self.assertEqual(s2.slug, "ace-printers-2")

    def test_slug_stable_on_update(self):
        shop = Shop.objects.create(name="Ace Printers", owner=_make_user())
        original_slug = shop.slug
        shop.name = "Ace Printers Ltd"
        shop.save()
        shop.refresh_from_db()
        self.assertEqual(shop.slug, original_slug)

    def test_blank_slug_regenerates(self):
        shop = Shop.objects.create(name="Ace Printers", owner=_make_user())
        shop.slug = ""
        shop.save()
        shop.refresh_from_db()
        self.assertTrue(shop.slug.startswith("ace-printers"))

    def test_non_ascii_slug(self):
        shop = Shop.objects.create(name="Ünïcödé Shöp", owner=_make_user())
        self.assertEqual(shop.slug, "unicode-shop")

    def test_very_long_name_truncated(self):
        long_name = "A" * 200
        shop = Shop.objects.create(name=long_name, owner=_make_user())
        self.assertLessEqual(len(shop.slug), 100)

    def test_empty_name_gets_fallback(self):
        shop = Shop.objects.create(name="", owner=_make_user())
        self.assertTrue(shop.slug)  # should get "item" fallback

    def test_manual_slug_preserved(self):
        shop = Shop.objects.create(
            name="Manual", slug="custom-slug", owner=_make_user()
        )
        self.assertEqual(shop.slug, "custom-slug")


class SlugUtilityTests(TestCase):
    """Test the generate_unique_slug utility directly."""

    def test_basic_slugify(self):
        from common.slug import generate_unique_slug

        slug = generate_unique_slug(Shop, "Hello World!")
        self.assertEqual(slug, "hello-world")

    def test_collision_appends_suffix(self):
        from common.slug import generate_unique_slug

        owner = _make_user()
        Shop.objects.create(name="test", slug="hello", owner=owner)
        slug = generate_unique_slug(Shop, "Hello")
        self.assertEqual(slug, "hello-2")

    def test_excludes_own_pk(self):
        from common.slug import generate_unique_slug

        owner = _make_user()
        shop = Shop.objects.create(name="test", slug="hello", owner=owner)
        slug = generate_unique_slug(Shop, "Hello", instance_pk=shop.pk)
        self.assertEqual(slug, "hello")
