from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from api.permissions import IsSuperUser

User = get_user_model()


class IsSuperUserPermissionTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.permission = IsSuperUser()
        self.user = User.objects.create_user(email="user@test.com", password="pass12345")
        self.superuser = User.objects.create_superuser(email="admin@test.com", password="pass12345")

    def test_denies_anonymous_user(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()
        self.assertFalse(self.permission.has_permission(request, view=None))

    def test_denies_authenticated_non_superuser(self):
        request = self.factory.get("/")
        request.user = self.user
        self.assertFalse(self.permission.has_permission(request, view=None))

    def test_allows_authenticated_superuser(self):
        request = self.factory.get("/")
        request.user = self.superuser
        self.assertTrue(self.permission.has_permission(request, view=None))
