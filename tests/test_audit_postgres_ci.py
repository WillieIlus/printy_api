"""Audit #10d: the suite also runs on PostgreSQL (production's engine).

* config/test_settings.py: TEST_DB_ENGINE=postgresql switches the test
  database to PostgreSQL (DB_* variables); SQLite stays the zero-setup default.
* .github/workflows/ci.yml: a second job runs both runners against a
  PostgreSQL service container; the SQLite job is kept; no secrets.
* The first PostgreSQL run found a real bug: the admin custody-release and
  dispute-resolve views used select_for_update() together with
  select_related() over nullable foreign keys, which PostgreSQL rejects
  ("FOR UPDATE cannot be applied to the nullable side of an outer join"), so
  both endpoints returned 500 in production. SQLite ignores FOR UPDATE.
* On PostgreSQL, a real two-connection test shows initiate_stk_push is
  serialised by its row lock (SQLite cannot show this).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from decimal import Decimal
from pathlib import Path
from unittest import mock, skipUnless

import yaml
from django.db import connection, connections
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

REPO_ROOT = Path(__file__).resolve().parents[1]
ON_POSTGRES = connection.vendor == "postgresql"


class CiWorkflowTests(SimpleTestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
        self.text = (REPO_ROOT / ".github/workflows/ci.yml").read_text()

    def test_sqlite_job_is_kept(self):
        self.assertIn("test", self.workflow["jobs"])
        steps = " ".join(str(step.get("run", "")) for step in self.workflow["jobs"]["test"]["steps"])
        self.assertIn("python -m pytest", steps)
        self.assertIn("manage.py test", steps)

    def test_postgres_job_runs_both_runners_against_a_service_container(self):
        job = self.workflow["jobs"]["test-postgres"]
        service = job["services"]["postgres"]
        self.assertTrue(str(service["image"]).startswith("postgres:"))
        self.assertIn("--health-cmd", str(service.get("options", "")))
        self.assertEqual(str(job["env"]["TEST_DB_ENGINE"]), "postgresql")
        steps = " ".join(str(step.get("run", "")) for step in job["steps"])
        self.assertIn("python -m pytest", steps)
        self.assertIn("manage.py test", steps)
        self.assertIn("makemigrations --check", steps)

    def test_no_repository_secret_is_used(self):
        self.assertNotIn("secrets.", self.text)


_SETTINGS_SCRIPT = textwrap.dedent(
    """
    import os, sys
    try:
        import django
        django.setup()
    except Exception as exc:
        print("BOOT_FAILED:" + type(exc).__name__ + ":" + str(exc))
        sys.exit(0)
    from django.conf import settings
    print("ENGINE=" + settings.DATABASES["default"]["ENGINE"])
    """
)


def _boot_test_settings(extra):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/tmp",
        "DJANGO_SETTINGS_MODULE": "config.test_settings",
        **extra,
    }
    result = subprocess.run(
        [sys.executable, "-c", _SETTINGS_SCRIPT], capture_output=True, text=True, timeout=180, env=env, cwd=str(REPO_ROOT)
    )
    return (result.stdout or "") + (result.stderr or "")


class TestDatabaseSwitchTests(SimpleTestCase):
    def test_default_is_sqlite(self):
        self.assertIn("ENGINE=django.db.backends.sqlite3", _boot_test_settings({}))

    def test_postgresql_switch(self):
        self.assertIn("ENGINE=django.db.backends.postgresql", _boot_test_settings({"TEST_DB_ENGINE": "postgresql"}))

    def test_unknown_engine_is_refused(self):
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:TEST_DB_ENGINE", _boot_test_settings({"TEST_DB_ENGINE": "mysql"}))


class AdminJobLocksOnlyTheJobRowTests(TestCase):
    """DB-independent guard for the PostgreSQL-only 500 (see module doc)."""

    def setUp(self):
        from accounts.models import User

        self.admin = User.objects.create_superuser(email="lock-admin@test.com", password="pass12345")
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

    def _lock_calls(self, url, payload):
        calls = []
        original = QuerySet.select_for_update

        def spy(qs, *args, **kwargs):
            calls.append((qs.model.__name__, kwargs.get("of", ())))
            return original(qs, *args, **kwargs)

        with mock.patch.object(QuerySet, "select_for_update", autospec=True, side_effect=spy):
            self.api.post(url, payload, format="json")
        return [of for model, of in calls if model == "ManagedJob"]

    def test_custody_release_locks_with_of_self(self):
        locks = self._lock_calls("/api/admin/custody/999999/release/", {})
        self.assertTrue(locks, "view did not lock the job")
        self.assertTrue(all("self" in of for of in locks), locks)

    def test_dispute_resolve_locks_with_of_self(self):
        locks = self._lock_calls("/api/admin/disputes/999999/resolve/", {"action": "dismiss"})
        self.assertTrue(locks, "view did not lock the job")
        self.assertTrue(all("self" in of for of in locks), locks)


@skipUnless(ON_POSTGRES, "row locks are only real on PostgreSQL (TEST_DB_ENGINE=postgresql)")
@override_settings(MPESA_FORCE_STUB="1")
class StkPushRowLockOnPostgresTests(TransactionTestCase):
    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from accounts.models import User
        from payments.services import create_payment_for_quote
        from pricing.models import PlatformFeePolicy
        from pricing.services.platform_fee_policy import create_quote_financial_split
        from quotes.choices import QuoteOfferStatus
        from quotes.models import ProductionOption, Quote, QuoteRequest
        from shops.models import Shop

        client_user = User.objects.create_user(email="pg-client@test.com", password="pass", role=User.Role.CLIENT)
        broker = User.objects.create_user(email="pg-broker@test.com", password="pass", role=User.Role.PARTNER)
        owner = User.objects.create_user(email="pg-owner@test.com", password="pass", role=User.Role.PRODUCTION)
        shop = Shop.objects.create(name="PG shop", owner=owner, is_active=True)
        PlatformFeePolicy.objects.create(name="PG policy", is_active=True)
        quote_request = QuoteRequest.objects.create(
            created_by=client_user, customer_name="Client", customer_email="pg-client@test.com", customer_phone="+254700000000"
        )
        option = ProductionOption.objects.create(
            quote_request=quote_request,
            shop=shop,
            production_cost=Decimal("1000.00"),
            created_by=broker,
            status=ProductionOption.SELECTED,
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=shop,
            production_option=option,
            created_by=broker,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1750.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        create_quote_financial_split(
            quote=quote, production_cost=Decimal("1000.00"), broker_client_price=Decimal("1750.00"), production_option=option
        )
        self.payment = create_payment_for_quote(quote=quote, payer=client_user, payer_phone="254700000000")

    def test_two_concurrent_pushes_create_one_prompt(self):
        from payments import services
        from payments.models import MpesaSTKRequest, Payment

        original_expire = services.expire_stale_stk_requests

        def slow_expire(*args, **kwargs):
            result = original_expire(*args, **kwargs)
            time.sleep(0.5)  # widen the check-then-create window
            return result

        barrier = threading.Barrier(2)
        errors = []

        def push():
            try:
                barrier.wait(timeout=10)
                payment = Payment.objects.get(pk=self.payment.pk)
                services.initiate_stk_push(payment=payment, phone_number="254712345678")
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                connections.close_all()

        with mock.patch.object(services, "expire_stale_stk_requests", side_effect=slow_expire):
            threads = [threading.Thread(target=push) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(MpesaSTKRequest.objects.filter(payment_id=self.payment.pk).count(), 1)
