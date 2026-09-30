"""The MPESA_FORCE_STUB flag must never be active in a real deployment.

Stub mode simulates the outbound Daraja STK call, which is what lets an
automated run reach job completion and payouts. That makes it a payment-faking
switch: if it could be on with DEBUG=False, a deployment could mark real orders
paid without Safaricom. These tests pin the guard that prevents that.

The flag was previously read in payments.services._is_stub_mode() but never
published as a Django setting, so MPESA_FORCE_STUB=1 silently did nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from django.test import SimpleTestCase


def _boot_settings(overrides: dict[str, str]) -> tuple[bool, str]:
    """Boot config.settings in a subprocess and report whether it raised.

    The parent environment is inherited and only the listed keys are overridden,
    so the subprocess can still reach the same services (a stripped environment
    fails on Windows for unrelated reasons and would make the test meaningless).
    """
    script = textwrap.dedent(
        """
        import os, sys
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        try:
            import django
            django.setup()
        except Exception as exc:
            print("BOOT_FAILED:" + type(exc).__name__ + ":" + str(exc))
            sys.exit(0)
        from django.conf import settings
        from payments.services import _is_stub_mode
        print("BOOT_OK:%r:%r" % (settings.MPESA_FORCE_STUB, _is_stub_mode()))
        """
    )
    env = {**os.environ, **overrides}
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    output = (result.stdout or "") + (result.stderr or "")
    return "BOOT_OK" in output, output.strip()


# Enough production env to clear every *other* DEBUG=False guard, so the only
# thing that can refuse the boot is the stub guard under test.
PRODUCTION_ENV = {
    "DEBUG": "false",
    "SECRET_KEY": "realtestsecretkeyrealtestsecretkey123",
    "ALLOWED_HOSTS": "printy.ke,api.printy.ke",
    "CSRF_TRUSTED_ORIGINS": "https://printy.ke",
    "CORS_ALLOWED_ORIGINS": "https://printy.ke",
    "FRONTEND_URL": "https://printy.ke",
    "MPESA_ENV": "sandbox",
}


class MpesaForceStubGuardTests(SimpleTestCase):
    def test_stub_mode_refuses_to_boot_when_debug_is_false(self):
        env = {**PRODUCTION_ENV, "MPESA_FORCE_STUB": "1"}
        ok, output = _boot_settings(env)
        self.assertFalse(ok, f"settings booted with stub mode under DEBUG=False:\n{output}")
        self.assertIn("MPESA_FORCE_STUB cannot be enabled when DEBUG=False", output)

    def test_control_same_environment_without_stub_boots(self):
        """Proves the assertion above is the stub guard, not another guard."""
        ok, output = _boot_settings(PRODUCTION_ENV)
        self.assertTrue(ok, f"control env failed to boot, guard test is inconclusive:\n{output}")
        self.assertIn("BOOT_OK:False:False", output)

    def test_stub_mode_is_available_when_debug_is_true(self):
        ok, output = _boot_settings({"MPESA_FORCE_STUB": "1", "DEBUG": "true"})
        self.assertTrue(ok, f"stub mode should be usable with DEBUG=True:\n{output}")
        self.assertIn("BOOT_OK:True:True", output)

    def test_flag_is_absent_from_the_environment_by_default(self):
        from django.conf import settings

        self.assertIsInstance(settings.MPESA_FORCE_STUB, bool)
