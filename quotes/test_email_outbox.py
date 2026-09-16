from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import User
from quotes.choices import QuoteStatus
from quotes.messaging import create_quote_message
from quotes.models import EmailOutbox, QuoteRequest, QuoteRequestMessage
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EmailOutboxCommandTestCase(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="outbox-client@test.com",
            password="pass12345",
            role="client",
            name="Outbox Client",
        )
        self.shop_owner = User.objects.create_user(
            email="outbox-shop@test.com",
            password="pass12345",
            role="shop_owner",
            name="Outbox Shop Owner",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Outbox Shop",
            slug="outbox-shop",
            is_active=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            customer_name="Outbox Client",
            customer_email=self.client_user.email,
            status=QuoteStatus.SUBMITTED,
        )
        self.message = QuoteRequestMessage.objects.create(
            quote_request=self.quote_request,
            sender=self.shop_owner,
            recipient=self.client_user,
            recipient_email=self.client_user.email,
            sender_role=QuoteRequestMessage.SenderRole.SHOP,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
            message_kind=QuoteRequestMessage.MessageKind.QUOTE,
            message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
            direction=QuoteRequestMessage.Direction.INBOUND,
            subject="Outbox quote update",
            body="Your quote is ready.",
        )

    def _create_outbox(self, **overrides):
        defaults = {
            "recipient": self.client_user,
            "recipient_email": self.client_user.email,
            "subject": "Outbox quote update",
            "body": "Your quote is ready.",
            "quote_request": self.quote_request,
            "message": self.message,
        }
        defaults.update(overrides)
        return EmailOutbox.objects.create(**defaults)

    @patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_process_email_outbox_marks_successful_send(self, mock_send):
        outbox = self._create_outbox()

        call_command("process_email_outbox")

        outbox.refresh_from_db()
        self.message.refresh_from_db()
        self.assertEqual(outbox.status, EmailOutbox.Status.SENT)
        self.assertEqual(outbox.attempts_count, 1)
        self.assertIsNotNone(outbox.sent_at)
        self.assertEqual(outbox.last_error, "")
        self.assertTrue(self.message.email_sent)
        self.assertEqual(self.message.email_status, QuoteRequestMessage.EmailStatus.SENT)
        mock_send.assert_called_once_with(fail_silently=False)

    @patch("quotes.messaging.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp down"))
    def test_process_email_outbox_marks_failed_send_for_retry(self, mock_send):
        outbox = self._create_outbox()

        call_command("process_email_outbox")

        outbox.refresh_from_db()
        self.message.refresh_from_db()
        self.assertEqual(outbox.status, EmailOutbox.Status.FAILED)
        self.assertEqual(outbox.attempts_count, 1)
        self.assertEqual(outbox.last_error, "smtp down")
        self.assertFalse(self.message.email_sent)
        self.assertEqual(self.message.email_status, QuoteRequestMessage.EmailStatus.FAILED)
        self.assertEqual(self.message.email_error, "smtp down")
        mock_send.assert_called_once_with(fail_silently=False)

    @patch("quotes.messaging.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp still down"))
    def test_process_email_outbox_marks_permanently_failed_at_max_attempts(self, mock_send):
        outbox = self._create_outbox(
            status=EmailOutbox.Status.FAILED,
            attempts_count=EmailOutbox.MAX_ATTEMPTS - 1,
        )

        call_command("process_email_outbox")

        outbox.refresh_from_db()
        self.message.refresh_from_db()
        self.assertEqual(outbox.status, EmailOutbox.Status.PERMANENTLY_FAILED)
        self.assertEqual(outbox.attempts_count, EmailOutbox.MAX_ATTEMPTS)
        self.assertEqual(outbox.last_error, "smtp still down")
        self.assertFalse(self.message.email_sent)
        self.assertEqual(self.message.email_status, QuoteRequestMessage.EmailStatus.FAILED)
        mock_send.assert_called_once_with(fail_silently=False)

    @patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_create_quote_message_auto_sends_outbox_after_commit(self, mock_send):
        with self.captureOnCommitCallbacks(execute=True):
            message = create_quote_message(
                quote_request=self.quote_request,
                sender=self.shop_owner,
                recipient=self.client_user,
                sender_role=QuoteRequestMessage.SenderRole.SHOP,
                recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
                message_kind=QuoteRequestMessage.MessageKind.QUOTE,
                message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
                direction=QuoteRequestMessage.Direction.INBOUND,
                subject="Auto-send quote update",
                body="Your quote is ready.",
                send_email_copy=True,
            )

        outbox = EmailOutbox.objects.get(message=message)
        message.refresh_from_db()
        self.assertEqual(outbox.status, EmailOutbox.Status.SENT)
        self.assertEqual(outbox.attempts_count, 1)
        self.assertIsNotNone(outbox.sent_at)
        self.assertTrue(message.email_sent)
        self.assertEqual(message.email_status, QuoteRequestMessage.EmailStatus.SENT)
        mock_send.assert_called_once_with(fail_silently=False)

    @override_settings(EMAIL_OUTBOX_AUTO_SEND=False)
    @patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_create_quote_message_can_queue_without_immediate_send(self, mock_send):
        with self.captureOnCommitCallbacks(execute=True):
            message = create_quote_message(
                quote_request=self.quote_request,
                sender=self.shop_owner,
                recipient=self.client_user,
                sender_role=QuoteRequestMessage.SenderRole.SHOP,
                recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
                message_kind=QuoteRequestMessage.MessageKind.QUOTE,
                message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
                direction=QuoteRequestMessage.Direction.INBOUND,
                subject="Queued quote update",
                body="Your quote is queued.",
                send_email_copy=True,
            )

        outbox = EmailOutbox.objects.get(message=message)
        message.refresh_from_db()
        self.assertEqual(outbox.status, EmailOutbox.Status.PENDING)
        self.assertEqual(outbox.attempts_count, 0)
        self.assertFalse(message.email_sent)
        self.assertEqual(message.email_status, QuoteRequestMessage.EmailStatus.NOT_SENT)
        mock_send.assert_not_called()

    @patch("quotes.messaging.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp down"))
    def test_create_quote_message_auto_send_failure_keeps_retryable_outbox(self, mock_send):
        with self.captureOnCommitCallbacks(execute=True):
            message = create_quote_message(
                quote_request=self.quote_request,
                sender=self.shop_owner,
                recipient=self.client_user,
                sender_role=QuoteRequestMessage.SenderRole.SHOP,
                recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
                message_kind=QuoteRequestMessage.MessageKind.QUOTE,
                message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
                direction=QuoteRequestMessage.Direction.INBOUND,
                subject="Failing quote update",
                body="Your quote should retry.",
                send_email_copy=True,
            )

        outbox = EmailOutbox.objects.get(message=message)
        message.refresh_from_db()
        self.assertEqual(outbox.status, EmailOutbox.Status.FAILED)
        self.assertEqual(outbox.attempts_count, 1)
        self.assertEqual(outbox.last_error, "smtp down")
        self.assertFalse(message.email_sent)
        self.assertEqual(message.email_status, QuoteRequestMessage.EmailStatus.FAILED)
        self.assertEqual(message.email_error, "smtp down")
        mock_send.assert_called_once_with(fail_silently=False)

