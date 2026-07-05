from django.core.management.base import BaseCommand

from accounts.services.system_accounts import ensure_house_broker_user


class Command(BaseCommand):
    help = "Create or update the Printy house broker user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="house-broker@printy.ke",
            help="Email for the house broker account.",
        )

    def handle(self, *args, **options):
        user, profile, created = ensure_house_broker_user(email=options["email"])
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} Printy house broker user #{user.id} ({user.email})."))
        self.stdout.write(f"PRINTY_HOUSE_BROKER_USER_ID={user.id}")
        self.stdout.write(f"broker_profile_active={profile.broker_profile_active}")
