from __future__ import annotations

from allauth.account.models import EmailAddress
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User


def _mask_email(email: str) -> str:
    value = (email or "").strip()
    if "@" not in value:
        return "***"
    local, _, domain = value.partition("@")
    keep = local[:1] if len(local) <= 2 else local[:2]
    return f"{keep}***@{domain}"


class Command(BaseCommand):
    help = (
        "Normalise stored user and EmailAddress emails to lowercase so that login "
        "is case-insensitive. DRY RUN by default; pass --apply to write. Accounts "
        "whose addresses collide case-insensitively are left untouched for manual "
        "review and are never merged or deleted."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist the normalised emails. Without this flag nothing is written.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show proposed changes without writing them (default).",
        )

    def handle(self, *args, **options):
        apply = bool(options["apply"]) and not bool(options["dry_run"])
        mode_label = "APPLY" if apply else "DRY RUN"

        user_changes, user_collisions = self._proposed_user_changes()
        address_changes, address_collisions = self._proposed_address_changes()

        scanned_users = User.objects.exclude(email__isnull=True).exclude(email="").count()
        scanned_addresses = EmailAddress.objects.count()

        self.stdout.write(
            f"{mode_label}: scanned {scanned_users} user(s) and "
            f"{scanned_addresses} email address(es)."
        )
        self.stdout.write(
            f"{mode_label}: {len(user_changes)} user email change(s), "
            f"{len(address_changes)} email address change(s) proposed; "
            f"{len(user_collisions) + len(address_collisions)} collision group(s) skipped."
        )

        for user, new_email in user_changes:
            self.stdout.write(f"- user={user.id} {_mask_email(user.email)} -> {_mask_email(new_email)}")
        for address, new_email in address_changes:
            self.stdout.write(f"- email_address={address.id} {_mask_email(address.email)} -> {_mask_email(new_email)}")
        for key, ids in user_collisions:
            self.stdout.write(
                self.style.WARNING(
                    f"- skipped user collision {_mask_email(key)} for user ids {ids}"
                )
            )
        for key, ids in address_collisions:
            self.stdout.write(
                self.style.WARNING(
                    f"- skipped email address collision {_mask_email(key)} for address ids {ids}"
                )
            )

        if not apply:
            self.stdout.write(
                self.style.WARNING("Dry run complete. Re-run with --apply to persist changes.")
            )
            return

        with transaction.atomic():
            for user, new_email in user_changes:
                User.objects.filter(pk=user.pk).update(email=new_email)
            for address, new_email in address_changes:
                EmailAddress.objects.filter(pk=address.pk).update(email=new_email)

        self.stdout.write(self.style.SUCCESS("Email normalisation complete."))

    def _proposed_user_changes(self):
        changes = []
        collisions = []
        grouped: dict[str, list[User]] = {}
        users = User.objects.exclude(email__isnull=True).exclude(email="").order_by("id")
        for user in users:
            key = user.email.strip().lower()
            grouped.setdefault(key, []).append(user)

        for key, group in grouped.items():
            if len(group) > 1:
                collisions.append((key, [u.id for u in group]))
                continue
            user = group[0]
            if user.email != key:
                changes.append((user, key))
        return changes, collisions

    def _proposed_address_changes(self):
        changes = []
        collisions = []
        grouped: dict[tuple[int, str], list[EmailAddress]] = {}
        for address in EmailAddress.objects.all().order_by("id"):
            key = (address.user_id, (address.email or "").strip().lower())
            grouped.setdefault(key, []).append(address)

        for (_, normalized), group in grouped.items():
            if len(group) > 1:
                collisions.append((normalized, [a.id for a in group]))
                continue
            address = group[0]
            if address.email != normalized:
                changes.append((address, normalized))
        return changes, collisions
