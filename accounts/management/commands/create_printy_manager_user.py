from django.core.management.base import BaseCommand

from accounts.services.system_accounts import ensure_printy_manager_user
from pricing.services.platform_fee_policy import get_active_platform_fee_policy


class Command(BaseCommand):
    help = "Create or update the Printy fallback manager system user."

    def handle(self, *args, **options):
        user, profile, created = ensure_printy_manager_user()
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} Printy manager user #{user.id} ({user.email})."))
        self.stdout.write(f"PRINTY_MANAGER_USER_ID={user.id}")
        self.stdout.write(f"policy_maximum_manager_markup_multiple={get_active_platform_fee_policy().maximum_manager_markup_multiple}")
