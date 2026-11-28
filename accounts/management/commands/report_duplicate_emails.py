"""Read-only report of accounts whose emails collide case-insensitively (audit #4).

Google login now matches accounts with ``email__iexact`` and refuses (409) when
an address maps to more than one account, so these groups need a human
decision. This command never merges, edits or deletes anything.

    python manage.py report_duplicate_emails            # human-readable
    python manage.py report_duplicate_emails --fail-if-any   # exit 1 when found (CI)

Equivalent SQL::

    SELECT LOWER(email), COUNT(*) FROM accounts_user
    WHERE email IS NOT NULL AND email <> ''
    GROUP BY LOWER(email) HAVING COUNT(*) > 1;
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.db.models.functions import Lower

from accounts.models import User


def duplicate_email_groups() -> dict[str, list[User]]:
    keys = (
        User.objects.exclude(email__isnull=True)
        .exclude(email="")
        .annotate(email_key=Lower("email"))
        .values("email_key")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("email_key", flat=True)
    )
    groups: dict[str, list[User]] = {}
    for key in keys:
        groups[key] = list(User.objects.filter(email__iexact=key).order_by("id"))
    return groups


def _mask(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:2]}***@{domain}"


class Command(BaseCommand):
    help = "List accounts whose emails differ only by case. Read-only; never merges."

    def add_arguments(self, parser):
        parser.add_argument("--fail-if-any", action="store_true", help="Exit non-zero when duplicates exist.")
        parser.add_argument("--unmasked", action="store_true", help="Print full email addresses.")

    def handle(self, *args, **options):
        from allauth.socialaccount.models import SocialAccount

        groups = duplicate_email_groups()
        show = (lambda e: e) if options["unmasked"] else _mask
        self.stdout.write(f"{len(groups)} duplicate email group(s) found (case-insensitive).")
        for key, users in groups.items():
            self.stdout.write(self.style.WARNING(f"- {show(key)}: {len(users)} account(s)"))
            for user in users:
                providers = ",".join(
                    SocialAccount.objects.filter(user=user).values_list("provider", flat=True)
                ) or "-"
                self.stdout.write(
                    f"    id={user.id} email={show(user.email)} role={user.role} "
                    f"active={user.is_active} staff={user.is_staff} superuser={user.is_superuser} "
                    f"social={providers} last_login={user.last_login or 'never'} "
                    f"joined={user.date_joined:%Y-%m-%d}"
                )
        if groups and options["fail_if_any"]:
            raise CommandError(f"{len(groups)} duplicate email group(s) need manual review.")
