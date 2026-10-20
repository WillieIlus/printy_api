import io

from allauth.account.models import EmailAddress
from django.core.management import call_command
from django.test import TestCase

from accounts.models import User


class NormalizeUserEmailsCommandTests(TestCase):
    def _legacy_user(self, email, password="Pass12345"):
        """Create a user, then force the pre-fix mixed-case email into the row."""
        user = User.objects.create_user(
            email=email.lower(),
            password=password,
            name="Legacy",
            role=User.Role.CLIENT,
        )
        User.objects.filter(pk=user.pk).update(email=email)
        user.refresh_from_db()
        return user

    def test_default_is_dry_run_and_writes_nothing(self):
        user = self._legacy_user("Legacy.Mixed@Example.com")
        EmailAddress.objects.create(
            user=user, email="Legacy.Mixed@Example.com", primary=True, verified=True
        )

        out = io.StringIO()
        call_command("normalize_user_emails", stdout=out)
        self.assertIn("DRY RUN", out.getvalue())

        user.refresh_from_db()
        self.assertEqual(user.email, "Legacy.Mixed@Example.com")
        self.assertTrue(
            EmailAddress.objects.filter(email="Legacy.Mixed@Example.com").exists()
        )

    def test_apply_normalises_user_and_address_rows(self):
        user = self._legacy_user("Legacy.Mixed@Example.com")
        EmailAddress.objects.create(
            user=user, email="Legacy.Mixed@Example.com", primary=True, verified=True
        )

        out = io.StringIO()
        call_command("normalize_user_emails", "--apply", stdout=out)
        self.assertIn("APPLY", out.getvalue())

        user.refresh_from_db()
        self.assertEqual(user.email, "legacy.mixed@example.com")
        self.assertTrue(
            EmailAddress.objects.filter(user=user, email="legacy.mixed@example.com").exists()
        )

    def test_case_insensitive_collision_group_is_left_for_manual_review(self):
        first = self._legacy_user("Dup@Example.com")
        second = self._legacy_user("dup@example.com")

        out = io.StringIO()
        call_command("normalize_user_emails", "--apply", stdout=out)
        self.assertIn("skipped", out.getvalue().lower())

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.email, "Dup@Example.com")
        self.assertEqual(second.email, "dup@example.com")

    def test_command_is_idempotent(self):
        user = self._legacy_user("Legacy.Mixed@Example.com")
        EmailAddress.objects.create(
            user=user, email="Legacy.Mixed@Example.com", primary=True, verified=True
        )

        call_command("normalize_user_emails", "--apply", stdout=io.StringIO())

        out = io.StringIO()
        call_command("normalize_user_emails", "--apply", stdout=out)
        self.assertIn("0 user email change(s), 0 email address change(s)", out.getvalue())
