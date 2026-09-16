# pytest-django should be configured via environment variables in CI/dev:
#   $env:DJANGO_SETTINGS_MODULE='config.test_settings'
#   $env:PYTHONPATH='.'
#
# This conftest pins them so `python -m pytest` works without shell setup.
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")