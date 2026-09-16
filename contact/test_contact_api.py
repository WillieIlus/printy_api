"""Phase 7.5b — the public contact-form submission endpoint.

The /contact page has a form where visitors ask about quotes, joining as
a printer, or anything else. Phase 7 found the old submit handler was
faked with `window.setTimeout` — no backend existed. This module adds the
DB model and a throttled AllowAny POST endpoint, and this test file pins:

1. A valid submission is persisted with a 201 and echoed back.
2. Invalid input (missing / short fields, bad email) is rejected with 400.
3. The optional `topic` field defaults to 'quote' when omitted.
4. An authenticated submitter records the user FK automatically.
5. The endpoint is reachable without authentication (AllowAny).
6. Unknown topics are rejected.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from contact.models import ContactMessage

User = get_user_model()


class ContactSubmissionApiTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/contact/submit/"
        self.valid_payload = {
            "topic": "partner",
            "name": "Ava Lindqvist",
            "email": "ava@printy.ke",
            "message": "I run a print shop in Industrial Area and want to join.",
        }

    def test_valid_submission_returns_201_and_persists(self):
        response = self.client.post(self.url, self.valid_payload, format="json")

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["topic"], "partner")
        self.assertEqual(data["name"], "Ava Lindqvist")
        self.assertEqual(data["email"], "ava@printy.ke")
        self.assertTrue(ContactMessage.objects.filter(pk=data["id"]).exists())

    def test_defaults_to_quote_topic_when_omitted(self):
        payload = {**self.valid_payload}
        payload.pop("topic")
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["topic"], "quote")
        self.assertTrue(ContactMessage.objects.filter(topic="quote").exists())

    def test_missing_name_is_rejected(self):
        payload = {**self.valid_payload, "name": ""}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContactMessage.objects.exists())

    def test_short_name_is_rejected(self):
        payload = {**self.valid_payload, "name": "A"}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_email_is_rejected(self):
        payload = {**self.valid_payload, "email": "not-an-email"}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_message_too_short_is_rejected(self):
        payload = {**self.valid_payload, "message": "hi"}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_unknown_topic_is_rejected(self):
        payload = {**self.valid_payload, "topic": "banana"}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_authenticated_submitter_is_recorded(self):
        user = User.objects.create_user(email="staff@test.com", password="pw")
        self.client.force_authenticate(user=user)
        response = self.client.post(self.url, self.valid_payload, format="json")

        self.assertEqual(response.status_code, 201)
        message = ContactMessage.objects.get(pk=response.json()["id"])
        self.assertEqual(message.user_id, user.id)

    def test_anonymous_submit_records_no_user(self):
        response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, 201)
        message = ContactMessage.objects.get(pk=response.json()["id"])
        self.assertIsNone(message.user_id)