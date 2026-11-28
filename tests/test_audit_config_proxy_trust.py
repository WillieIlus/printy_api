"""Audit #10b: config and proxy trust.

* One resolver for the real client IP (common.client_ip). A raw
  X-Forwarded-For is believed only when the direct peer is a trusted proxy
  (MPESA_TRUSTED_PROXIES). Before, DRF's stock throttles (default Anon/User/
  Scoped, analytics, guest quote, tracking lookups), clients.views._client_ip,
  api.payment_views._client_ip and common.request_meta.get_client_ip all took
  the left-most X-Forwarded-For from anyone.
* Production needs a shared cache (REDIS_URL) and explicit ALLOWED_HOSTS; both
  fail closed at startup.
* Ad-hoc emails (signup admin notice, missing-artwork reminder) are sent after
  commit, never inside the transaction.
"""
from __future__ import annotations

import itertools
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import transaction
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from common.throttling import TrustedIdentMixin

REPO_ROOT = Path(__file__).resolve().parents[1]
ATTACKER = "198.51.100.7"  # direct peer, not a proxy


def _low_rates(**rates):
    base = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    base.update(rates)
    return {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": base}


_counter = itertools.count(1)


def _spoofed():
    n = next(_counter)
    return f"10.{n // 65536 % 256}.{n // 256 % 256}.{n % 256}"


# ---------------------------------------------------------------------------
# The shared resolver and every caller
# ---------------------------------------------------------------------------


class ClientIpResolutionTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def _request(self, remote, xff=None, real_ip=None):
        extra = {"REMOTE_ADDR": remote}
        if xff is not None:
            extra["HTTP_X_FORWARDED_FOR"] = xff
        if real_ip is not None:
            extra["HTTP_X_REAL_IP"] = real_ip
        return self.rf.get("/", **extra)

    def _callers(self):
        from api.payment_views import _client_ip as payment_client_ip
        from clients.views import _client_ip as clients_client_ip
        from common.client_ip import resolve_client_ip
        from common.request_meta import get_client_ip
        from payments.callback_security import resolve_client_ip as callback_resolve

        return {
            "common.client_ip.resolve_client_ip": resolve_client_ip,
            "payments.callback_security.resolve_client_ip": callback_resolve,
            "clients.views._client_ip": clients_client_ip,
            "api.payment_views._client_ip": payment_client_ip,
            "common.request_meta.get_client_ip": get_client_ip,
        }

    @override_settings(MPESA_TRUSTED_PROXIES=["127.0.0.1"])
    def test_spoofed_headers_from_a_direct_peer_are_ignored_everywhere(self):
        request = self._request(ATTACKER, xff="1.2.3.4", real_ip="5.6.7.8")
        for name, func in self._callers().items():
            with self.subTest(caller=name):
                self.assertEqual(func(request), ATTACKER)

    @override_settings(MPESA_TRUSTED_PROXIES=["127.0.0.1", "10.0.0.0/8"])
    def test_behind_trusted_proxies_the_first_untrusted_hop_is_the_client(self):
        # nginx appends the real peer; whatever the client wrote is to its left.
        request = self._request("127.0.0.1", xff="1.2.3.4, 203.0.113.9, 10.0.0.5")
        for name, func in self._callers().items():
            with self.subTest(caller=name):
                self.assertEqual(func(request), "203.0.113.9")

    @override_settings(MPESA_TRUSTED_PROXIES=["127.0.0.1"])
    def test_garbage_hop_behind_a_proxy_is_not_guessed(self):
        self.assertIsNone(self._callers()["common.client_ip.resolve_client_ip"](self._request("127.0.0.1", xff="nope")))

    def test_no_code_reads_the_forwarded_header_outside_the_resolver(self):
        offenders = []
        for path in REPO_ROOT.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith((".venv/", "node_modules/")) or "/test" in f"/{rel}" or rel.startswith("tests/"):
                continue
            if rel in {"common/client_ip.py", "conftest.py"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in ('"HTTP_X_FORWARDED_FOR"', "'HTTP_X_FORWARDED_FOR'", '"HTTP_X_REAL_IP"'):
                if needle in text:
                    offenders.append(f"{rel}: {needle}")
        self.assertEqual(offenders, [])


def _iter_view_classes(patterns=None):
    patterns = get_resolver().url_patterns if patterns is None else patterns
    for entry in patterns:
        if isinstance(entry, URLResolver):
            yield from _iter_view_classes(entry.url_patterns)
        elif isinstance(entry, URLPattern):
            callback = entry.callback
            cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
            if cls is not None:
                yield cls


class EveryThrottleUsesTheResolverTests(SimpleTestCase):
    def test_default_throttle_classes_are_trusted(self):
        for throttle in api_settings.DEFAULT_THROTTLE_CLASSES:
            with self.subTest(throttle=throttle):
                self.assertTrue(issubclass(throttle, TrustedIdentMixin), throttle)

    def test_every_routed_view_throttle_is_trusted(self):
        seen = set()
        for view in _iter_view_classes():
            for throttle in getattr(view, "throttle_classes", None) or ():
                seen.add(throttle)
                with self.subTest(view=view.__name__, throttle=throttle.__name__):
                    self.assertTrue(issubclass(throttle, TrustedIdentMixin))
        self.assertGreater(len(seen), 5)

    @override_settings(MPESA_TRUSTED_PROXIES=["127.0.0.1"])
    def test_spoofed_header_does_not_change_any_ip_throttle_key(self):
        from api.throttling import AnalyticsEventThrottle, GuestQuoteRequestThrottle, TrackLookupIPThrottle
        from clients.throttling import ClientLinkViewThrottle, ClientOtpRequestThrottle, ClientOtpVerifyThrottle
        from common.throttling import TrustedAnonRateThrottle, TrustedScopedRateThrottle
        from django.contrib.auth.models import AnonymousUser
        from rest_framework.request import Request

        rf = RequestFactory()

        def key(throttle_cls, xff):
            request = Request(rf.get("/", REMOTE_ADDR=ATTACKER, HTTP_X_FORWARDED_FOR=xff))
            request.user = AnonymousUser()
            view = mock.Mock(throttle_scope="contact_submit", kwargs={})
            throttle = throttle_cls()
            if isinstance(throttle, TrustedScopedRateThrottle):
                throttle.scope = "contact_submit"
            return throttle.get_cache_key(request, view)

        for throttle_cls in (
            TrustedAnonRateThrottle,
            TrustedScopedRateThrottle,
            AnalyticsEventThrottle,
            GuestQuoteRequestThrottle,
            TrackLookupIPThrottle,
            ClientLinkViewThrottle,
            ClientOtpRequestThrottle,
            ClientOtpVerifyThrottle,
        ):
            with self.subTest(throttle=throttle_cls.__name__):
                first, second = key(throttle_cls, "1.1.1.1"), key(throttle_cls, "2.2.2.2")
                self.assertEqual(first, second)
                self.assertIn(ATTACKER, first)


class SpoofedForwardedForEndToEndTests(TestCase):
    """Rotating X-Forwarded-For per request must not reset the counter."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _codes(self, method, url, n, *, remote=ATTACKER, xff=None, **payload):
        client = APIClient()
        codes = []
        for _ in range(n):
            header = xff() if callable(xff) else (xff or _spoofed())
            response = getattr(client, method)(url, payload or None, format="json", REMOTE_ADDR=remote, HTTP_X_FORWARDED_FOR=header)
            codes.append(response.status_code)
        return codes

    def test_default_scoped_throttle_contact_form(self):
        with override_settings(REST_FRAMEWORK=_low_rates(contact_submit="2/hour")):
            codes = self._codes("post", "/api/contact/submit/", 3)
        self.assertNotIn(429, codes[:2])
        self.assertEqual(codes[2], 429)

    def test_tracking_lookup_ip_throttle(self):
        with override_settings(REST_FRAMEWORK=_low_rates(track_lookup_ip="2/min")):
            client = APIClient()
            codes = [
                client.get(f"/api/track/NOPE{i}/", REMOTE_ADDR=ATTACKER, HTTP_X_FORWARDED_FOR=_spoofed()).status_code
                for i in range(3)
            ]
        self.assertNotIn(429, codes[:2])
        self.assertEqual(codes[2], 429)

    def test_auth_login_throttle(self):
        with override_settings(REST_FRAMEWORK=_low_rates(auth_login="2/min")):
            codes = self._codes("post", "/api/auth/login/", 3, email="x@test.ke", password="nope")
        self.assertEqual(codes[2], 429)

    @override_settings(MPESA_TRUSTED_PROXIES=["127.0.0.1"])
    def test_behind_the_proxy_a_prepended_value_does_not_help(self):
        with override_settings(REST_FRAMEWORK=_low_rates(contact_submit="2/hour")):
            codes = self._codes(
                "post", "/api/contact/submit/", 3, remote="127.0.0.1", xff=lambda: f"{_spoofed()}, 203.0.113.9"
            )
        self.assertEqual(codes[2], 429)

    @override_settings(MPESA_TRUSTED_PROXIES=["127.0.0.1"])
    def test_behind_the_proxy_distinct_real_clients_keep_separate_buckets(self):
        with override_settings(REST_FRAMEWORK=_low_rates(contact_submit="2/hour")):
            codes = self._codes("post", "/api/contact/submit/", 3, remote="127.0.0.1", xff=_spoofed)
        self.assertNotIn(429, codes)


# ---------------------------------------------------------------------------
# Production settings fail closed: shared cache, ALLOWED_HOSTS
# ---------------------------------------------------------------------------

_BOOT_SCRIPT = textwrap.dedent(
    """
    import sys
    try:
        import django
        django.setup()
    except Exception as exc:
        print("BOOT_FAILED:" + type(exc).__name__ + ":" + str(exc))
        sys.exit(0)
    from django.conf import settings
    print("BOOT_OK|" + settings.CACHES["default"]["BACKEND"] + "|" + ",".join(settings.ALLOWED_HOSTS))
    """
)

PRODUCTION_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings",
    "DEBUG": "false",
    "APP_ENV": "production",
    "SECRET_KEY": "prod-like-secret-key-for-boot-tests-0123456789abcdef0123456789",
    "ALLOWED_HOSTS": "api.printy.ke",
    "CSRF_TRUSTED_ORIGINS": "https://printy.ke",
    "CORS_ALLOWED_ORIGINS": "https://printy.ke",
    "FRONTEND_URL": "https://printy.ke",
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "MPESA_ENV": "sandbox",
    "MPESA_CALLBACK_SECRET": "boot-test-callback-secret-0123456789abcdefgh",
    "REDIS_URL": "redis://127.0.0.1:6379/1",
}


def _boot(env: dict[str, str]) -> str:
    """Import config.settings in a clean process (no inherited env, no .env)."""
    clean = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp", **env}
    result = subprocess.run(
        [sys.executable, "-c", _BOOT_SCRIPT],
        capture_output=True,
        text=True,
        timeout=180,
        env=clean,
        cwd=str(REPO_ROOT),
    )
    return ((result.stdout or "") + (result.stderr or "")).strip()


def _without(env, *names):
    return {k: v for k, v in env.items() if k not in names}


@mock.patch.dict(os.environ, {})
class ProductionSettingsFailClosedTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if (REPO_ROOT / ".env").exists():
            raise cls.failureException(".env present; boot tests would read it")

    def test_control_full_production_env_boots_with_redis(self):
        output = _boot(PRODUCTION_ENV)
        self.assertIn("BOOT_OK|django.core.cache.backends.redis.RedisCache|api.printy.ke", output)

    def test_missing_redis_url_refuses_to_boot(self):
        output = _boot(_without(PRODUCTION_ENV, "REDIS_URL"))
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:REDIS_URL must be set when DEBUG=False", output)

    def test_bad_redis_scheme_refuses_to_boot(self):
        output = _boot({**PRODUCTION_ENV, "REDIS_URL": "http://cache:6379"})
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:REDIS_URL must start with", output)

    def test_debug_without_redis_uses_locmem(self):
        output = _boot({**_without(PRODUCTION_ENV, "REDIS_URL", "ALLOWED_HOSTS"), "DEBUG": "true", "APP_ENV": "local"})
        self.assertIn("BOOT_OK|django.core.cache.backends.locmem.LocMemCache|localhost,127.0.0.1,[::1],testserver", output)

    def test_missing_allowed_hosts_refuses_to_boot(self):
        output = _boot(_without(PRODUCTION_ENV, "ALLOWED_HOSTS"))
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:ALLOWED_HOSTS must be set explicitly", output)

    def test_empty_allowed_hosts_refuses_to_boot(self):
        output = _boot({**PRODUCTION_ENV, "ALLOWED_HOSTS": " , "})
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:ALLOWED_HOSTS must be set explicitly", output)

    def test_wildcard_allowed_hosts_refuses_to_boot(self):
        output = _boot({**PRODUCTION_ENV, "ALLOWED_HOSTS": "*"})
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:ALLOWED_HOSTS must not contain a wildcard", output)

    def test_local_only_allowed_hosts_refuses_to_boot(self):
        output = _boot({**PRODUCTION_ENV, "ALLOWED_HOSTS": "localhost,127.0.0.1"})
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:ALLOWED_HOSTS must include the real production hosts", output)


class SharedCacheSystemCheckTests(SimpleTestCase):
    def _evaluate(self, **kwargs):
        from config.settings import _evaluate_shared_cache

        return [m.id for m in _evaluate_shared_cache(**kwargs)]

    def test_locmem_is_an_error_when_a_shared_cache_is_required(self):
        caches = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
        self.assertEqual(self._evaluate(required=True, caches=caches), ["printy.E020"])
        self.assertEqual(self._evaluate(required=False, caches=caches), [])

    def test_redis_passes(self):
        caches = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache"}}
        self.assertEqual(self._evaluate(required=True, caches=caches), [])

    def test_num_proxies_is_reported_as_ignored(self):
        caches = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache"}}
        self.assertEqual(self._evaluate(required=True, caches=caches, num_proxies="1"), ["printy.W903"])

    def test_test_settings_use_an_in_process_cache(self):
        self.assertEqual(settings.CACHES["default"]["BACKEND"], "django.core.cache.backends.locmem.LocMemCache")
        self.assertFalse(settings.SHARED_CACHE_REQUIRED)


# ---------------------------------------------------------------------------
# No SMTP inside a transaction
# ---------------------------------------------------------------------------


@override_settings(ADMIN_NOTIFY_EMAIL="ops@printy.test")
class SignupAdminEmailAfterCommitTests(TestCase):
    def _signal(self):
        from allauth.account.signals import user_signed_up
        from accounts.models import User

        user = User.objects.create_user(email="new-signup@test.com", password="pass12345")
        request = RequestFactory().get("/", HTTP_HOST="api.printy.ke")
        user_signed_up.send(sender=User, request=request, user=user)

    def test_not_sent_inside_the_transaction(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with transaction.atomic():
                self._signal()
            self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(len(callbacks), 1)

    def test_sent_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._signal()
        self.assertEqual([m.to for m in mail.outbox], [["ops@printy.test"]])
        self.assertIn("new-signup@test.com", mail.outbox[0].subject)

    def test_smtp_failure_is_logged_not_raised(self):
        with mock.patch("notifications.services.send_mail", side_effect=OSError("smtp down")):
            with self.assertLogs("notifications.services", level="WARNING"):
                with self.captureOnCommitCallbacks(execute=True):
                    self._signal()


class MissingArtworkReminderAfterCommitTests(TestCase):
    def _send(self):
        from accounts.models import User
        from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent, CalculatorDraftStatus
        from quotes.models import CalculatorDraft
        from quotes.services_workflow import send_calculator_draft_to_shops

        client_user = User.objects.create_user(
            email="reminder-client@test.com", password="pass12345", role=User.Role.CLIENT, name="Reminder Client"
        )
        draft = CalculatorDraft.objects.create(
            user=client_user,
            title="No artwork yet",
            status=CalculatorDraftStatus.DRAFT,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.CLIENT_QUOTE_REQUEST,
            calculator_inputs_snapshot={"product_type": "flyer", "quantity": 100},
        )
        return send_calculator_draft_to_shops(draft=draft, shops=[], request_details_snapshot={"customer_name": "R"})

    def _reminders(self):
        return [m for m in mail.outbox if m.subject.startswith("Upload your artwork")]

    def test_reminder_is_not_sent_inside_the_submission_transaction(self):
        with self.captureOnCommitCallbacks(execute=False):
            self._send()
            self.assertEqual(self._reminders(), [])

    def test_reminder_is_sent_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._send()
        self.assertEqual([m.to for m in self._reminders()], [["reminder-client@test.com"]])
