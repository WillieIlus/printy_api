# pytest-django reads DJANGO_SETTINGS_MODULE from pytest.ini; this default
# also covers running pytest from an unusual working directory.
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")

# config/test_settings.py matches python_files = test*.py but is a settings
# module, not a test module.
collect_ignore = ["config/test_settings.py"]
