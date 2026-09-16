"""Tests for DRF permission system."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from core.permissions import is_seller, is_buyer, IsSellerOrReadOnly, IsBuyerOrSeller
from shops.models import Shop

User = get_user_model()


class PermissionHelpersTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email='owner@test.com', password='pass')
        self.buyer = User.objects.create_user(email='buyer@test.com', password='pass')
        self.shop = Shop.objects.create(name='Test Shop', slug='test-shop', owner=self.owner)

    def test_is_seller_owner(self):
        self.assertTrue(is_seller(self.owner, self.shop))

    def test_is_seller_buyer(self):
        self.assertFalse(is_seller(self.buyer, self.shop))

    def test_is_seller_by_pk(self):
        self.assertTrue(is_seller(self.owner, self.shop.pk))

    def test_is_buyer_owner(self):
        self.assertFalse(is_buyer(self.owner, self.shop))

    def test_is_buyer_buyer(self):
        self.assertTrue(is_buyer(self.buyer, self.shop))


class IsSellerOrReadOnlyTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(email='owner@test.com', password='pass')
        self.buyer = User.objects.create_user(email='buyer@test.com', password='pass')
        self.shop = Shop.objects.create(name='Test Shop', slug='test-shop', owner=self.owner)
        self.perm = IsSellerOrReadOnly()

    def test_safe_method_allowed_unauthenticated(self):
        request = self.factory.get('/')
        request.user = AnonymousUser()
        # Mock view with shop_pk
        class View:
            kwargs = {'shop_pk': 1}
        self.assertTrue(self.perm.has_permission(request, View()))

    def test_post_seller_allowed(self):
        request = self.factory.post('/')
        request.user = self.owner
        class View:
            kwargs = {'shop_pk': self.shop.pk}
        self.assertTrue(self.perm.has_permission(request, View()))

    def test_post_buyer_denied(self):
        request = self.factory.post('/')
        request.user = self.buyer
        class View:
            kwargs = {'shop_pk': self.shop.pk}
        self.assertFalse(self.perm.has_permission(request, View()))
