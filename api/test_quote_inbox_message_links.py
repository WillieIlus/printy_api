"""Inbox message action links are normalised out of the legacy /dashboard/* scheme."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from api.quote_serializers import QuoteInboxMessageSerializer
from quotes.models import QuoteRequest, QuoteRequestMessage


User = get_user_model()


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class QuoteInboxMessageActionUrlTestCase(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.client_user = User.objects.create_user(
            email="inbox-client@example.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            customer_name="Inbox Client",
            customer_email=self.client_user.email,
        )

    def serialize_action_url(self, message):
        request = self.factory.get("/api/client/messages/")
        request.user = self.client_user
        return QuoteInboxMessageSerializer(message, context={"request": request}).data["action_url"]

    def test_legacy_stored_quote_link_is_normalised_for_client(self):
        message = QuoteRequestMessage.objects.create(
            quote_request=self.quote_request,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
            subject="Test Shop sent you a quote",
            body="Here is your quote.",
            metadata={"action_url": "/dashboard/client/requests/2/quote/1"},
        )
        self.assertEqual(self.serialize_action_url(message), "/app/buyer?tab=quote")

    def test_routed_stored_link_passes_through(self):
        message = QuoteRequestMessage.objects.create(
            quote_request=self.quote_request,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
            subject="Test Shop sent you a quote",
            body="Here is your quote.",
            metadata={"action_url": "/app/buyer?tab=quote"},
        )
        self.assertEqual(self.serialize_action_url(message), "/app/buyer?tab=quote")

    def test_missing_action_url_returns_empty(self):
        message = QuoteRequestMessage.objects.create(
            quote_request=self.quote_request,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
            subject="No link here",
            body="Plain message.",
            metadata={},
        )
        self.assertEqual(self.serialize_action_url(message), "")