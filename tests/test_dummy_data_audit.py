"""RUN 10 - dummy-data regression guards (release-readiness audit).

Release-readiness audit item 1.5: a regression test that fails when forbidden
dummy data (placeholder / fake / sample / lorem / demo strings, hardcoded
manager/printer/shop names, hardcoded prices) leaks into the migration history
or into the clean-DB / production seed paths.

Rules enforced here:

* No Django migration may contain dummy-data markers (the schema must stay free
  of test/demo fixtures).
* The clean-DB e2e reset command must not carry dummy account/shop markers.
* The production deploy script must not invoke any seed management command.

The only dummy data allowed anywhere in the product is the explicitly labelled
Track-my-job sample flow (design-sample components); these guards keep backend
seed/migration paths free of it.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories that are never part of the product source tree.
SKIPPED_PARTS = {
    "__pycache__",
    "node_modules",
    ".git",
    "venv",
    ".venv",
    "site-packages",
    "Lib",
}


def _iter_migration_files() -> list[Path]:
    found: list[Path] = []
    for path in sorted(REPO_ROOT.rglob("*/migrations/*.py")):
        if any(part in SKIPPED_PARTS for part in path.parts):
            continue
        found.append(path)
    return found

MIGRATION_FORBIDDEN_TOKENS = (
    "demo",
    "sample",
    "dummy",
    "fake",
    "placeholder",
    "lorem",
    "coming soon",
)

RESET_E2E_FORBIDDEN_STRINGS = (
    "Printy Demo Production",
    "Printy Test Manager",
    "manager@printy.local",
    "shop@printy.local",
    "admin@printy.local",
    "@printy.local",
)

SEED_INVOCATION = re.compile(r"manage\.py\s+seed[\w\s\-]*", re.IGNORECASE)


class NoDummyDataInMigrationsTests(SimpleTestCase):
    """Migrations must never create dummy/test data."""

    def test_no_migration_contains_dummy_tokens(self) -> None:
        migration_files = _iter_migration_files()
        self.assertTrue(
            migration_files,
            msg="No migration files were discovered under the repo root.",
        )
        for path in migration_files:
            text = path.read_text(encoding="utf-8").lower()
            for token in MIGRATION_FORBIDDEN_TOKENS:
                self.assertNotIn(
                    token,
                    text,
                    msg=f"Forbidden dummy token {token!r} found in migration {path}",
                )


class CleanResetCommandHasNoDummyDataTests(SimpleTestCase):
    """The clean-DB e2e reset command must not seed dummy accounts or shops."""

    def test_reset_e2e_command_has_no_dummy_accounts_or_shops(self) -> None:
        reset_path = REPO_ROOT / "core" / "management" / "commands" / "reset_e2e.py"
        self.assertTrue(reset_path.is_file(), msg="reset_e2e management command is missing")
        text = reset_path.read_text(encoding="utf-8")
        for forbidden in RESET_E2E_FORBIDDEN_STRINGS:
            self.assertNotIn(
                forbidden,
                text,
                msg=f"Forbidden dummy string {forbidden!r} found in reset_e2e command",
            )


class ProductionDeployScriptRunsNoSeedCommandTests(SimpleTestCase):
    """The production deploy script must never invoke a seed command."""

    def test_deploy_script_runs_no_seed_command(self) -> None:
        deploy_path = REPO_ROOT / "scripts" / "deploy.sh"
        self.assertTrue(deploy_path.is_file(), msg="scripts/deploy.sh is missing")
        text = deploy_path.read_text(encoding="utf-8")
        self.assertIsNone(
            SEED_INVOCATION.search(text),
            msg="scripts/deploy.sh must not run a seed management command.",
        )
