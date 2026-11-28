"""Audit #10c: repo hygiene guards.

Cheap checks that keep the cleanup from regressing: no UTF-8 BOM or NUL byte
in any Python file, the removed stub apps stay removed, and the files that
must never be committed are ignored by Git.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.apps import apps
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "venv", "env", "node_modules", ".git", "__pycache__"}


def _python_files():
    for path in REPO_ROOT.rglob("*.py"):
        if SKIP_DIRS.intersection(path.relative_to(REPO_ROOT).parts):
            continue
        yield path


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)


HAS_GIT = shutil.which("git") is not None and (REPO_ROOT / ".git").exists()


class PythonSourceHygieneTests(SimpleTestCase):
    def test_no_python_file_starts_with_a_utf8_bom(self):
        offenders = [str(p.relative_to(REPO_ROOT)) for p in _python_files() if p.read_bytes().startswith(b"\xef\xbb\xbf")]
        self.assertEqual(offenders, [])

    def test_no_python_file_contains_nul_bytes(self):
        offenders = [str(p.relative_to(REPO_ROOT)) for p in _python_files() if b"\x00" in p.read_bytes()]
        self.assertEqual(offenders, [])

    def test_forensics_scratch_script_is_gone(self):
        self.assertFalse((REPO_ROOT / "_forensics_tmp.py").exists())

    def test_dead_stub_apps_are_gone_and_not_installed(self):
        for name in ("gallery", "locations"):
            with self.subTest(app=name):
                self.assertFalse((REPO_ROOT / name).exists())
                self.assertFalse(apps.is_installed(name))


@skipUnless(HAS_GIT, "needs a git checkout")
class GitIgnoreTests(SimpleTestCase):
    MUST_BE_IGNORED = [
        "printy_test.sqlite3",
        "db.sqlite3",
        "other.sqlite3",
        "printy_test.sqlite3-journal",
        ".env",
        ".env.production",
        "accounts/__pycache__/models.cpython-311.pyc",
        "media/uploads/artwork.pdf",
        ".idea/workspace.xml",
        ".vscode/settings.json",
        "printy.code-workspace",
    ]
    MUST_NOT_BE_IGNORED = [".env.example", ".env.local.example", "config/settings.py"]

    def test_generated_and_secret_files_are_ignored(self):
        for path in self.MUST_BE_IGNORED:
            with self.subTest(path=path):
                self.assertEqual(_git("check-ignore", "-q", "--no-index", path).returncode, 0, path)

    def test_templates_and_source_are_not_ignored(self):
        for path in self.MUST_NOT_BE_IGNORED:
            with self.subTest(path=path):
                self.assertNotEqual(_git("check-ignore", "-q", "--no-index", path).returncode, 0, path)

    def test_no_sqlite_database_or_env_file_is_tracked(self):
        tracked = _git("ls-files").stdout.splitlines()
        offenders = [
            path
            for path in tracked
            if path.endswith((".sqlite3", ".sqlite", ".sqlite3-journal"))
            or Path(path).name == ".env"
            or "/__pycache__/" in f"/{path}"
        ]
        self.assertEqual(offenders, [])
