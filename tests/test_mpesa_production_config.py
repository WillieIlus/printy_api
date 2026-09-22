"""M-Pesa / Daraja production-readiness guards and regression tests.

Mirrors the email-delivery tests: the backend must refuse to boot when
MPESA_ENV=production is paired with missing/placeholder Daraja credentials,
instead of only failing at the first real payment.
"""
from django.core import checks
from django.core.checks import ERROR
from django.test import TestCase, override_settings

from config.settings import (
    check_mpesa_production_config,
    _evaluate_mpesa_production_config,
)
from mpesa_payments.services import MpesaConfigError, validate_production_config

REAL_KEY = "fQZk3lTkMw0Z3xTnU3bsJMEQ4cXjKqG8"
REAL_SECRET = "sKd0xPq9YmVnBt7wRzIaCfEkHuJgZl2A"
REAL_PASSKEY = "bfb279f9aa9bdbcf158e97dd9a467eb2e0c893865b4d38451f6f1e5f6e5f6e5f"
REAL_SHORTCODE = "174379"
CANONICAL_CALLBACK = "https://api.printy.ke/api/payments/mpesa/callback/"


def _config(**overrides):
    values = {
        "env": "sandbox",
        "consumer_key": "",
        "consumer_secret": "",
        "shortcode": "",
        "passkey": "",
        "callback_url": "",
    }
    values.update(overrides)
    return _evaluate_mpesa_production_config(**values)


def _error_ids(messages):
    return {m.id for m in messages if m.level >= ERROR}


class TestMpesaConfigCheck:
    """Pure-function contract of the Daraja production guard."""

    def test_production_with_placeholder_credentials_errors(self):
        ids = _error_ids(
            _config(
                env="production",
                consumer_key="replace-with-prod-consumer-key",
                consumer_secret="replace-with-prod-consumer-secret",
                shortcode="replace-with-prod-shortcode",
                passkey="replace-with-prod-passkey",
                callback_url=CANONICAL_CALLBACK,
            )
        )
        assert "printy.E013" in ids
        assert "printy.E014" in ids
        assert "printy.E015" in ids
        assert "printy.E016" in ids

    def test_production_with_missing_credentials_errors(self):
        ids = _error_ids(
            _config(
                env="production",
                consumer_key="",
                consumer_secret="",
                shortcode="",
                passkey="",
                callback_url=CANONICAL_CALLBACK,
            )
        )
        assert {"printy.E013", "printy.E014", "printy.E015", "printy.E016"} <= ids

    def test_production_with_real_credentials_passes(self):
        ids = _error_ids(
            _config(
                env="production",
                consumer_key=REAL_KEY,
                consumer_secret=REAL_SECRET,
                shortcode=REAL_SHORTCODE,
                passkey=REAL_PASSKEY,
                callback_url=CANONICAL_CALLBACK,
            )
        )
        assert ids == set()

    def test_production_rejects_unsafe_callback_urls(self):
        base = dict(
            env="production",
            consumer_key=REAL_KEY,
            consumer_secret=REAL_SECRET,
            shortcode=REAL_SHORTCODE,
            passkey=REAL_PASSKEY,
        )
        assert "printy.E010" in _error_ids(_config(**base, callback_url=""))
        assert "printy.E011" in _error_ids(
            _config(**base, callback_url="http://api.printy.ke/api/payments/mpesa/callback/")
        )
        assert "printy.E012" in _error_ids(
            _config(**base, callback_url="https://localhost:8000/callback/")
        )

    def test_production_rejects_implausible_shortcode(self):
        base = dict(
            env="production",
            consumer_key=REAL_KEY,
            consumer_secret=REAL_SECRET,
            passkey=REAL_PASSKEY,
            callback_url=CANONICAL_CALLBACK,
        )
        for bad_shortcode in ("1234", "replace-with-prod-shortcode", "0123456789012"):
            ids = _error_ids(_config(**base, shortcode=bad_shortcode))
            assert "printy.E015" in ids, bad_shortcode

    def test_sandbox_never_errors_on_missing_credentials(self):
        messages = _config(
            env="sandbox",
            consumer_key="replace-with-sandbox-consumer-key",
            consumer_secret="",
            shortcode="174379",
            passkey="",
            callback_url="https://ngrok-free.app/callback/",
        )
        assert _error_ids(messages) == set()
        assert "printy.W902" in {m.id for m in messages}


class TestMpesaSystemCheckIsWired(TestCase):
    def test_system_check_stays_green_in_test_env(self):
        findings = check_mpesa_production_config()
        assert _error_ids(findings) == set()

        # Proves our check is actually registered and runs under run_checks;
        # test_settings pins MPESA_ENV=sandbox -> W902, never printy.E01x.
        reports = checks.run_checks(tags=[checks.Tags.compatibility])
        produced = {r.id for r in reports if r.id in {"printy.W902", "printy.E013"}}
        assert "printy.W902" in produced


class TestRuntimeValidateProductionConfig(TestCase):
    """The runtime safety net (services.validate_production_config)."""

    def _creds(self, *, env="sandbox", user=REAL_KEY, secret=REAL_SECRET,
               shortcode=REAL_SHORTCODE, passkey=REAL_PASSKEY,
               callback=CANONICAL_CALLBACK):
        return override_settings(
            MPESA_ENV=env,
            MPESA_CONSUMER_KEY=user,
            MPESA_CONSUMER_SECRET=secret,
            MPESA_SHORTCODE=shortcode,
            MPESA_PASSKEY=passkey,
            MPESA_CALLBACK_URL=callback,
        )

    def test_placeholder_credentials_raise_before_any_http_call(self):
        with self._creds(user="replace-with-prod-consumer-key"):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_missing_credentials_raise(self):
        with self._creds(user="", secret="", passkey=""):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_implausible_shortcode_raises(self):
        with self._creds(shortcode="1234"):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_real_credentials_pass_in_sandbox(self):
        with self._creds(env="sandbox"):
            validate_production_config()  # must not raise

    def test_real_credentials_pass_in_production(self):
        with self._creds(env="production"):
            validate_production_config()  # must not raise

    def test_production_rejects_http_callback(self):
        with self._creds(env="production", callback="http://api.printy.ke/cb/"):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_production_rejects_localhost_callback(self):
        with self._creds(env="production", callback="https://localhost:8000/cb/"):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()