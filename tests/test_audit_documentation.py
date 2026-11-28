"""Audit tier3: the documentation must be true of the code.

These tests tie the rewritten docs to the code, so the docs cannot silently
drift again:

* MPESA_STK_IN_FLIGHT_SECONDS: docs/env_vars.md documents it as an
  environment variable, but config/settings.py never read it (only
  getattr(settings, ..., 180)), so setting it did nothing. It is now read and
  validated. This is the one code change in this section.
* docs/CANONICAL_MODELS.md:
  - every model in the code is listed;
  - JobPayment / JobSettlementSplit stay deprecated and do not exist;
  - the worked fee examples equal what calculate_quote_financials returns;
  - the dead Batch 4 formula is gone.
* docs/api_contract.md and docs/API_URL_AUDIT.md: every /api/ path either
  resolves or is explicitly marked NOT ROUTED.
* README no longer presents law.txt as a file in the repo.
* env_vars.md, DARAJA_PRODUCTION_CHECKLIST.md and DEPLOY_NOTES.md cover the
  required items.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

LOCAL_APPS = {
    "accounts", "shops", "inventory", "pricing", "catalog", "quotes", "notifications", "clients", "api",
    "jobs", "production", "payments", "mpesa_payments", "contact", "workflow", "common", "core",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# MPESA_STK_IN_FLIGHT_SECONDS is a real environment variable now
# --------------------------------------------------------------------------
_BOOT = textwrap.dedent(
    """
    import sys
    try:
        import django
        django.setup()
    except Exception as exc:
        print("BOOT_FAILED:" + type(exc).__name__ + ":" + str(exc))
        sys.exit(0)
    from django.conf import settings
    from payments.services import stk_in_flight_window
    print("VALUE=" + str(settings.MPESA_STK_IN_FLIGHT_SECONDS))
    print("WINDOW=" + str(int(stk_in_flight_window().total_seconds())))
    """
)


def _boot(extra: dict) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp", "DJANGO_SETTINGS_MODULE": "config.test_settings", **extra}
    result = subprocess.run([sys.executable, "-c", _BOOT], capture_output=True, text=True, timeout=180, env=env, cwd=str(REPO_ROOT))
    return (result.stdout or "") + (result.stderr or "")


class StkInFlightEnvVarTests(SimpleTestCase):
    def test_default_is_180_seconds(self):
        out = _boot({})
        self.assertIn("VALUE=180", out)
        self.assertIn("WINDOW=180", out)

    def test_environment_variable_changes_the_window(self):
        out = _boot({"MPESA_STK_IN_FLIGHT_SECONDS": "240"})
        self.assertIn("VALUE=240", out)
        self.assertIn("WINDOW=240", out)

    def test_non_integer_is_refused_at_startup(self):
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:MPESA_STK_IN_FLIGHT_SECONDS", _boot({"MPESA_STK_IN_FLIGHT_SECONDS": "3min"}))

    def test_window_shorter_than_the_handset_prompt_is_refused(self):
        self.assertIn("BOOT_FAILED:ImproperlyConfigured:MPESA_STK_IN_FLIGHT_SECONDS", _boot({"MPESA_STK_IN_FLIGHT_SECONDS": "30"}))


# --------------------------------------------------------------------------
# CANONICAL_MODELS.md
# --------------------------------------------------------------------------
class CanonicalModelsDocTests(SimpleTestCase):
    def setUp(self):
        self.doc = _read(DOCS / "CANONICAL_MODELS.md")

    def test_every_model_in_the_code_is_listed(self):
        missing = sorted(
            model._meta.label
            for model in apps.get_models()
            if model._meta.app_label in LOCAL_APPS and not re.search(rf"\b{model.__name__}\b", self.doc)
        )
        self.assertEqual(missing, [], "add these models to docs/CANONICAL_MODELS.md")

    def test_job_payment_stays_deprecated_and_does_not_exist(self):
        self.assertRegex(self.doc, r"JobPayment[^\n]*\n?[^\n]*DEPRECATED|DEPRECATED[^\n]*JobPayment")
        model_names = {model.__name__ for model in apps.get_models()}
        self.assertNotIn("JobPayment", model_names)
        self.assertNotIn("JobSettlementSplit", model_names)

    def test_dead_batch4_formula_is_not_documented_as_live(self):
        self.assertNotIn("printy_fee = printer_side_fee + broker_margin_fee", self.doc)
        self.assertNotIn("if add_platform_fee_on_top is true, client_total", self.doc)
        for phrase in ("1.00 / 1.05 / 1.08", "min_markup_multiple", "origin manager", "never negative"):
            self.assertIn(phrase, self.doc)

    def test_worked_examples_match_the_code(self):
        from pricing.models import PlatformFeePolicy
        from pricing.services.platform_fee_policy import calculate_quote_financials

        rows = re.findall(
            r"^\| (\d+) \| (\d+) \| (tier_[abc]) \| (no|yes[^|]*) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|$",
            self.doc,
            flags=re.MULTILINE,
        )
        self.assertGreaterEqual(len(rows), 4, "worked-example table not found")
        policy = PlatformFeePolicy()  # unsaved: the model defaults
        for p, m, tier, waived, total, shop, manager, fee in rows:
            result = calculate_quote_financials(
                production_cost=Decimal(p), manager_markup=Decimal(m), policy=policy, fee_waived=waived.startswith("yes")
            )
            self.assertEqual(
                (result.pricing_tier, result.client_total, result.shop_payout, result.manager_payout, result.printy_fee),
                (tier, Decimal(total), Decimal(shop), Decimal(manager), Decimal(fee)),
                f"doc row P={p} M={m}",
            )

    def test_documented_minimum_markups_match_the_code(self):
        from pricing.models import PlatformFeePolicy
        from pricing.services.platform_fee_policy import minimum_manager_markup

        policy = PlatformFeePolicy()
        charged = [minimum_manager_markup(production_cost=p, policy=policy) for p in (800, 5000, 20000)]
        waived = [minimum_manager_markup(production_cost=p, policy=policy, fee_waived=True) for p in (800, 5000, 20000)]
        self.assertIn(
            f"{' / '.join(str(int(v)) for v in charged)} with the fee\ncharged, and {' / '.join(str(int(v)) for v in waived)} with the fee waived",
            self.doc,
        )


# --------------------------------------------------------------------------
# API docs: every path resolves or is marked NOT ROUTED
# --------------------------------------------------------------------------
_PATH_RE = re.compile(r"`(/api/[^`\s]*)`")


def _blocks(text: str):
    """Table rows individually, other paragraphs as a whole (code fences skipped)."""
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    for paragraph in re.split(r"\n\s*\n", text):
        lines = paragraph.split("\n")
        if all(line.startswith("|") for line in lines if line.strip()):
            yield from lines
        else:
            yield paragraph


def _resolves(path: str) -> bool:
    path = path.split("?")[0]
    for sample in ("1", "demo-shop"):
        candidate = re.sub(r"\{[^}]+\}|<[^>]+>", sample, path)
        try:
            resolve(candidate)
            return True
        except Resolver404:
            continue
    return False


class ApiDocRoutesTests(SimpleTestCase):
    def _check(self, name):
        problems = []
        for block in _blocks(_read(DOCS / name)):
            marked = "not routed" in block.lower()
            for path in _PATH_RE.findall(block):
                if not marked and not _resolves(path):
                    problems.append(path)
        self.assertEqual(problems, [], f"{name}: paths that do not resolve and are not marked NOT ROUTED")

    def test_api_contract_paths_resolve_or_are_marked(self):
        self._check("api_contract.md")

    def test_api_url_audit_paths_resolve_or_are_marked(self):
        self._check("API_URL_AUDIT.md")

    def test_known_stale_rows_are_fixed(self):
        doc = _read(DOCS / "api_contract.md")
        gallery_row = next(line for line in doc.splitlines() if "`/api/products/gallery/`" in line)
        self.assertIn("NOT ROUTED", gallery_row)
        catalog_row = next(line for line in doc.splitlines() if line.startswith("| GET | `/api/public/shops/{slug}/catalog/`"))
        self.assertIn("verified manager", catalog_row)
        self.assertNotIn("| No |", catalog_row)

    def test_shop_catalog_is_not_anonymous(self):
        from api.views import PublicShopViewSet
        from api.permissions import PrintManagerMarketplaceAccess  # noqa: F401  (import proves the name)

        self.assertIn("PrintManagerMarketplaceAccess", [c.__name__ for c in PublicShopViewSet.permission_classes])

    def test_callback_auth_and_checkout_id_changes_are_documented(self):
        audit = _read(DOCS / "API_URL_AUDIT.md")
        contract = _read(DOCS / "api_contract.md")
        for doc in (audit, contract):
            for needle in ("MPESA_CALLBACK_SECRET", "MPESA_TRUSTED_PROXIES", "403", "404", "409", "checkout_request_id"):
                self.assertIn(needle, doc)


# --------------------------------------------------------------------------
# README / env vars / Daraja checklist / deploy notes
# --------------------------------------------------------------------------
class ProseDocTests(SimpleTestCase):
    def test_readme_does_not_present_law_txt_as_present(self):
        readme = _read(REPO_ROOT / "README.md")
        self.assertFalse((REPO_ROOT / "law.txt").exists())
        self.assertNotIn("The fee-tier policy is defined in `law.txt`", readme)
        self.assertIn("not in this repository", readme)

    def test_env_vars_documents_each_variable_and_its_missing_behaviour(self):
        doc = _read(DOCS / "env_vars.md")
        summary = doc[doc.index("what happens if a security-critical variable is missing"):]
        for var in (
            "MPESA_CALLBACK_SECRET", "MPESA_CALLBACK_ALLOWED_IPS", "MPESA_TRUSTED_PROXIES", "MPESA_STK_IN_FLIGHT_SECONDS",
            "GOOGLE_CLIENT_ID", "GOOGLE_OAUTH_DEV_SKIP_AUDIENCE", "REDIS_URL", "ALLOWED_HOSTS", "NUM_PROXIES",
        ):
            self.assertIn(f"`{var}`", summary, var)
        settings_src = _read(REPO_ROOT / "config" / "settings.py")
        for var in ("MPESA_STK_IN_FLIGHT_SECONDS", "REDIS_URL", "ALLOWED_HOSTS", "MPESA_TRUSTED_PROXIES", "GOOGLE_OAUTH_DEV_SKIP_AUDIENCE"):
            self.assertRegex(settings_src, rf"_get_env(_list)?\(\s*\"{var}\"|os\.environ\.get\(\s*\"{var}\"", var)

    def test_daraja_checklist_covers_the_lifecycle(self):
        doc = _read(DOCS / "DARAJA_PRODUCTION_CHECKLIST.md")
        for needle in (
            "payments.Payment", "MpesaSTKRequest", "MPESA_CALLBACK_SECRET", "MPESA_CALLBACK_ALLOWED_IPS",
            "MPESA_TRUSTED_PROXIES", "needs_review", "resolve-review", "180", "Reachability is not authenticity",
            "has not been checked against", "Go-live reconciliation",
        ):
            self.assertIn(needle, doc, needle)
        self.assertNotIn("JobPayment.payment_status", doc)

    @override_settings(MPESA_STK_IN_FLIGHT_SECONDS=180)
    def test_documented_stk_window_is_the_code_default(self):
        from payments.services import stk_in_flight_window

        self.assertEqual(int(stk_in_flight_window().total_seconds()), 180)

    def test_deploy_notes_cover_the_required_steps(self):
        doc = _read(DOCS / "DEPLOY_NOTES.md")
        for needle in (
            "MPESA_CALLBACK_SECRET", "google-auth", "pip install -r requirements.txt", "migrate",
            "report_duplicate_emails", "report_financial_split_anomalies", "nginx", "[secret]",
            "Rotating `MPESA_CALLBACK_SECRET`", "Frontend follow-ups",
        ):
            self.assertIn(needle, doc, needle)
        for command in ("report_duplicate_emails", "report_financial_split_anomalies"):
            matches = list(REPO_ROOT.glob(f"*/management/commands/{command}.py"))
            self.assertTrue(matches, command)
