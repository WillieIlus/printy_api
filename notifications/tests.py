from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from jobs.models import ManagedJob
from notifications.models import Notification
from notifications.serializers import NotificationSerializer
from quotes.models import QuoteRequest
from shops.models import Shop


User = get_user_model()


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NotificationTargetRouteContractTestCase(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.client_user = User.objects.create_user(
            email="notify-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="notify-manager@example.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.production_user = User.objects.create_user(
            email="notify-production@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.admin = User.objects.create_superuser(
            email="notify-admin@example.com",
            password="pass12345",
        )
        self.shop = Shop.objects.create(
            owner=self.production_user,
            name="Notify Production Shop",
            slug="notify-production-shop",
            is_active=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Notify Client",
            customer_email=self.client_user.email,
        )
        self.managed_job = ManagedJob.objects.create(
            title="Notify managed job",
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.manager,
        )

    def serialize_target(self, notification):
        request = self.factory.get("/api/me/notifications/")
        request.user = notification.user
        return NotificationSerializer(notification, context={"request": request}).data["target_url"]

    def notification_for(self, user, object_type, object_id):
        return Notification.objects.create(
            user=user,
            notification_type=Notification.JOB_STATUS_UPDATED,
            object_type=object_type,
            object_id=object_id,
            message="Route contract test",
        )

    def test_quote_request_targets_stay_inside_role_dashboards(self):
        self.assertEqual(
            self.serialize_target(self.notification_for(self.client_user, "quote_request", self.quote_request.id)),
            f"/dashboard/client/quotes/{self.quote_request.id}",
        )
        self.assertEqual(
            self.serialize_target(self.notification_for(self.manager, "quote_request", self.quote_request.id)),
            f"/dashboard/partner/quotes/{self.quote_request.id}",
        )
        self.assertEqual(
            self.serialize_target(self.notification_for(self.admin, "quote_request", self.quote_request.id)),
            "/dashboard/admin",
        )

    def test_managed_job_targets_are_role_aware_dashboard_urls(self):
        self.assertEqual(
            self.serialize_target(self.notification_for(self.client_user, "managed_job", self.managed_job.id)),
            f"/dashboard/client/jobs/{self.managed_job.id}",
        )
        self.assertEqual(
            self.serialize_target(self.notification_for(self.manager, "managed_job", self.managed_job.id)),
            f"/dashboard/partner/jobs/{self.managed_job.id}",
        )
        self.assertEqual(
            self.serialize_target(self.notification_for(self.production_user, "managed_job", self.managed_job.id)),
            f"/dashboard/production/jobs/{self.managed_job.id}",
        )
        self.assertEqual(
            self.serialize_target(self.notification_for(self.admin, "managed_job", self.managed_job.id)),
            "/dashboard/admin",
        )

    def test_notification_targets_do_not_use_legacy_root_or_shops_routes(self):
        targets = [
            self.serialize_target(self.notification_for(self.client_user, "quote_request", self.quote_request.id)),
            self.serialize_target(self.notification_for(self.manager, "quote_request", self.quote_request.id)),
            self.serialize_target(self.notification_for(self.production_user, "managed_job", self.managed_job.id)),
        ]
        for target in targets:
            self.assertFalse(target.startswith("/quotes/"))
            self.assertNotIn("/dashboard/jobs/", target)
            self.assertNotIn("/dashboard/shops/", target)
