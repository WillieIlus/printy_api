"""Debug email delivery without guessing.

Runs an actual send_mail() through the CURRENTLY configured EMAIL_BACKEND so a
misconfigured server (e.g. console backend in production) is caught immediately
instead of silently not delivering mail.
"""
from datetime import datetime, timezone

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Send a test email through the configured EMAIL_BACKEND to verify real "
        "delivery. WARNING: with the console backend the email is only printed, "
        "never delivered."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            dest="to",
            default="",
            help="Recipient address (defaults to ADMIN_NOTIFY_EMAIL).",
        )
        parser.add_argument(
            "--subject",
            dest="subject",
            default="[Printy] SMTP delivery test",
            help="Subject of the test email.",
        )
        parser.add_argument(
            "--preflight",
            dest="preflight",
            action="store_true",
            help="Only open and close the SMTP connection (proves host/port/auth) without sending.",
        )

    def _recipient(self, to):
        if to:
            return to
        admin = getattr(settings, "ADMIN_NOTIFY_EMAIL", "") or ""
        if not admin:
            self.stderr.write(
                self.style.ERROR(
                    "No recipient: pass --to or set ADMIN_NOTIFY_EMAIL in .env."
                )
            )
            raise SystemExit(2)
        return admin

    def _summarize(self, to):
        self.stdout.write(f"EMAIL_BACKEND:  {settings.EMAIL_BACKEND}")
        self.stdout.write(
            f"EMAIL_HOST:      {settings.EMAIL_HOST}:{settings.EMAIL_PORT}  "
            f"TLS={settings.EMAIL_USE_TLS}"
        )
        self.stdout.write(f"FROM:            {getattr(settings, 'DEFAULT_FROM_EMAIL', '')}")
        self.stdout.write(f"TO:              {to}")

    def _verify_smtp_credentials(self):
        host_user = getattr(settings, "EMAIL_HOST_USER", "")
        host_password = getattr(settings, "EMAIL_HOST_PASSWORD", "")
        if not host_user or not host_password:
            self.stderr.write(
                self.style.ERROR(
                    "SMTP backend selected but EMAIL_HOST_USER/EMAIL_HOST_PASSWORD "
                    "are empty. Configure them in .env (see docs/EMAIL_DELIVERY.md)."
                )
            )
            raise SystemExit(1)

    def handle(self, *args, **opts):
        to = self._recipient(opts["to"])
        backend = settings.EMAIL_BACKEND
        is_console = backend.endswith("backends.console.EmailBackend")
        is_smtp = "backends.smtp.EmailBackend" in backend

        self._summarize(to)

        if is_console and not settings.EMAIL_IS_LOCAL:
            self.stderr.write(
                self.style.ERROR(
                    "Console backend while APP_ENV is not local: emails are PRINTED, "
                    "never delivered. Set EMAIL_BACKEND=django.core.mail.backends."
                    "smtp.EmailBackend in the deployed .env (printy.E001)."
                )
            )
            raise SystemExit(1)

        if is_smtp:
            self._verify_smtp_credentials()

        try:
            if opts["preflight"]:
                if is_console:
                    self.stdout.write(
                        self.style.WARNING(
                            "Console backend has no connection to preflight; "
                            "nothing was sent."
                        )
                    )
                    return
                connection = get_connection()
                connection.open()
                connection.close()
                self.stdout.write(
                    self.style.SUCCESS(
                        "Preflight OK: SMTP connection opened and closed successfully."
                    )
                )
                return

            body = (
                "This is an automated Printy email delivery test.\n\n"
                f"Sent at:  {datetime.now(timezone.utc).isoformat()}\n"
                f"Backend:  {settings.EMAIL_BACKEND}\n"
                f"Host:     {settings.EMAIL_HOST}:{settings.EMAIL_PORT} "
                f"(TLS={settings.EMAIL_USE_TLS})\n\n"
                "If you received this, SMTP delivery is working."
            )
            sent = send_mail(
                subject=opts["subject"],
                message=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[to],
                fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001 - surface the SMTP error to the operator
            self.stderr.write(self.style.ERROR(f"Email delivery FAILED: {exc}"))
            raise SystemExit(1) from exc

        if sent:
            self.stdout.write(
                self.style.SUCCESS(
                    "Email queued/sent successfully. Check the recipient inbox "
                    "and spam folder to confirm end-to-end delivery."
                )
            )
            return

        self.stderr.write(
            self.style.ERROR(
                "send_mail returned 0 (no messages sent). Check backend and credentials."
            )
        )
        raise SystemExit(1)