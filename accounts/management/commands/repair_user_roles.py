from __future__ import annotations

from django.core.management.base import BaseCommand

from accounts.models import User
from accounts.services.roles import (
    CANONICAL_PARTNER_ROLE,
    CANONICAL_PRODUCTION_ROLE,
    CANONICAL_SUPER_ADMIN_ROLE,
    assign_role,
    normalize_role_value,
    resolve_user_roles,
    sync_legacy_role,
)


class Command(BaseCommand):
    help = "Repair legacy or incorrect user.role values based on partner/shop signals."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show proposed changes without writing them.")
        parser.add_argument("--apply", action="store_true", help="Apply the proposed changes.")
        parser.add_argument("--include-superusers", action="store_true", help="Also inspect superusers.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"] or not options["apply"]
        include_superusers = options["include_superusers"]

        queryset = User.objects.all().order_by("id")
        if not include_superusers:
            queryset = queryset.filter(is_superuser=False)

        proposed_changes: list[tuple[User, list[str]]] = []
        for user in queryset:
            target_roles = self._target_roles_for_user(user)
            missing_roles = [role for role in target_roles if role not in resolve_user_roles(user)]
            if not missing_roles:
                continue
            proposed_changes.append((user, missing_roles))

        if not proposed_changes:
            self.stdout.write(self.style.SUCCESS("No user role repairs needed."))
            return

        mode_label = "DRY RUN" if dry_run else "APPLY"
        self.stdout.write(f"{mode_label}: {len(proposed_changes)} user role change(s) detected.")
        for user, roles_to_add in proposed_changes:
            self.stdout.write(f"- user={user.id} email={user.email} add roles {', '.join(roles_to_add)}")
            if not dry_run:
                for role in roles_to_add:
                    assign_role(user, role, source="repair_user_roles")
                sync_legacy_role(user)

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete. Re-run with --apply to persist changes."))
        else:
            self.stdout.write(self.style.SUCCESS("User role repair complete."))

    def _target_roles_for_user(self, user: User) -> list[str]:
        roles: list[str] = []
        legacy_role = normalize_role_value(user.role)
        if legacy_role:
            roles.append(legacy_role)
        if bool(user.partner_profile_enabled) and CANONICAL_PARTNER_ROLE not in roles:
            roles.append(CANONICAL_PARTNER_ROLE)
        if (self._owns_shop(user) or self._has_active_membership(user)) and CANONICAL_PRODUCTION_ROLE not in roles:
            roles.append(CANONICAL_PRODUCTION_ROLE)
        if (user.is_superuser or user.is_staff) and CANONICAL_SUPER_ADMIN_ROLE not in roles:
            roles.append(CANONICAL_SUPER_ADMIN_ROLE)
        if not roles:
            roles.append("client")
        return roles

    def _owns_shop(self, user: User) -> bool:
        owned_shops = getattr(user, "owned_shops", None)
        if owned_shops is None:
            return False
        try:
            return owned_shops.exists()
        except Exception:
            return False

    def _has_active_membership(self, user: User) -> bool:
        memberships = getattr(user, "shop_memberships", None)
        if memberships is None:
            return False
        try:
            return memberships.filter(is_active=True).exists()
        except Exception:
            return False
