"""Email-delivery guards and regression tests.

Covers the reported bug: emails "work" in the server console (console
EMAIL_BACKEND prints them) but are never delivered over SMTP because the
deployed .env never set EMAIL_BACKEND. These tests keep that from silently
regressing.
"""
import io
import uuid
from contextlib import redirect_stdout

from django.core import checks
from django.core import mail
from django.core.checks import ERROR
from django.core.mail import send_mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from config.settings import (
    EMAIL_IS_LOCAL,
    check_email_delivery_config,
    _evaluate_email_delivery,
)

CONSOLE = "django.core.mail.backends.console.EmailBackend"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
SMTP = "django.core.mail.backends.smtp.EmailBackend"


def _config(**overrides):
    values = {
        "backend": CONSOLE,
        "is_local": True,
        "host": "smtp.gmail.com",
        "host_user": "",
        "host_password": "",
    }
    values.update(overrides)
    return _evaluate_email_delivery(**values)


def _error_ids(messages):
    return {m.id for m in messages if m.level >= ERROR}


class TestEmailDeliveryConfigCheck:
    """Pure-function contract of the delivery guard."""

    def test_console_backend_in_production_is_a_startup_error(self):
        ids = _error_ids(_config(backend=CONSOLE, is_local=False))
        assert "printy.E001" in ids

    def test_console_backend_locally_is_only_informational(self):
        messages = _config(backend=CONSOLE, is_local=True)
        assert _error_ids(messages) == set()
        assert "printy.W901" in {m.id for m in messages}

    def test_smtp_without_credentials_is_a_startup_error(self):
        ids = _error_ids(
            _config(backend=SMTP, is_local=False, host_user="", host_password="")
        )
        assert "printy.E002" in ids
        assert "printy.E003" in ids

    def test_smtp_with_placeholder_credentials_is_a_startup_error(self):
        ids = _error_ids(
            _config(
                backend=SMTP,
                is_local=False,
                host_user="replace-with-smtp-user",
                host_password="replace-with-smtp-password",
            )
        )
        assert "printy.E002" in ids
        assert "printy.E003" in ids

    def test_smtp_without_host_is_a_startup_error(self):
        ids = _error_ids(
            _config(
                backend=SMTP,
                is_local=False,
                host="",
                host_user="no-reply@printy.ke",
                host_password="real-app-password",
            )
        )
        assert "printy.E004" in ids

    def test_smtp_with_real_credentials_passes(self):
        ids = _error_ids(
            _config(
                backend=SMTP,
                is_local=False,
                host="smtp.sendgrid.net",
                host_user="apikey",
                host_password="SG.real-secret",
            )
        )
        assert ids == set()

    def test_production_without_email_backend_var_cannot_pass_silently(self):
        # The exact reported failure: deployed .env forgets EMAIL_BACKEND, Django
        # falls back to the console default, SMTP creds are ignored. The guard
        # must flag it. is_local=False simulates APP_ENV=production.
        ids = _error_ids(
            _config(backend=CONSOLE, is_local=False, host_user="hello@printy.ke")
        )
        assert "printy.E001" in ids


class TestSystemCheckIsWired(TestCase):
    def test_system_check_runs_without_failing_in_test_env(self):
        # pytest-django sets DJANGO_SETTINGS_MODULE=config.test_settings, so the
        # guard treats the environment as local and must stay green.
        assert EMAIL_IS_LOCAL is True

        findings = check_email_delivery_config()
        assert _error_ids(findings) == set()

        # run_checks must actually execute our check; W901/E001 is produced
        # by check_email_delivery_config only.
        reports = checks.run_checks(tags=[checks.Tags.compatibility])
        produced = {r.id for r in reports if r.id in {"printy.W901", "printy.E001"}}
        assert "printy.W901" in produced


class TestEmailDeliveryBehavior(TestCase):
    """Proof of the bug and of the fix, exercised through Django's outbox."""

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_console_backend_prints_but_never_queues_deliverable_mail(self):
        sink = io.StringIO()
        with redirect_stdout(sink):
            send_mail(
                "Console subject",
                "Hello from console",
                "from@printy.ke",
                ["to@example.com"],
            )
        assert mail.outbox == []  # nothing queued for real delivery
        assert "Console subject" in sink.getvalue()  # only printed to console

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_real_backend_queues_deliverable_mail(self):
        with redirect_stdout(io.StringIO()):
            send_mail(
                "SMTP subject",
                "Hello over SMTP",
                "from@printy.ke",
                ["to@example.com"],
            )
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "SMTP subject"

    @override_settings(
        EMAIL_BACKEND=LOCMEM,
        ACCOUNT_EMAIL_VERIFICATION="mandatory",
        FRONTEND_URL="https://printy.ke",
    )
    def test_registration_flow_produces_deliverable_activation_email(self):
        email = f"delivery-{uuid.uuid4().hex[:8]}@test.com"
        response = APIClient().post(
            "/api/auth/register/",
            {"email": email, "password": "Pass12345", "name": "Delivery Test", "role": "client"},
            format="json",
        )
        assert response.status_code == 201
        assert len(mail.outbox) == 1
        assert "printy.ke/auth/confirm-email?key=" in mail.outbox[0].body
        assert "localhost:3000" not in mail.outbox[0].body


class TestSendTestEmailCommand(TestCase):
    @override_settings(
        EMAIL_BACKEND=CONSOLE,
        EMAIL_IS_LOCAL=False,
    )
    def test_command_fails_early_on_console_backend_in_production_config(self):
        # Simulate the command running on a server where the console backend was
        # left in place while the environment is not local (EMAIL_IS_LOCAL=False).
        with self.assertRaises(SystemExit) as ctx:
            call_command("send_test_email", "--to", "ops@printy.ke")
        assert ctx.exception.code != 0

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_command_warns_and_prints_under_console_backend(self):
        sink = io.StringIO()
        with redirect_stdout(sink):
            try:
                call_command("send_test_email", "--to", "ops@printy.ke")
            except SystemExit as exc:  # pragma: no cover - safety net
                assert exc.code == 0
        assert mail.outbox == []  # console backend: printed, never delivered

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_command_sends_a_deliverable_message_via_real_backend(self):
        call_command("send_test_email", "--to", "ops@printy.ke")
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["ops@printy.ke"]