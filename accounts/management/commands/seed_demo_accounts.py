"""seed_demo_accounts - create the canonical prototype demo accounts for printy_ui.

Mirrors the demo logins offered on the printy_ui sign-in screen:

    Buyer   ava@studionorth.co.ke   (client)
    Manager dale@printy.ke          (partner)
    Printer jon@northpress.co.ke    (production)
    Admin   admin@printy.ke         (super_admin)

All accounts use the shared demo password (default: "printy"), mark their
EmailAddress as verified (so sign-in works even with
ACCOUNT_EMAIL_VERIFICATION=mandatory), and are idempotent: an existing user
is never deleted or re-seeded with a new password.

Usage:
    python manage.py seed_demo_accounts
    python manage.py seed_demo_accounts --password=printy
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.services.roles import assign_role

DEMO_ACCOUNTS = [
    {
        "email": "ava@studionorth.co.ke",
        "name": "Ava Lindqvist",
        "role": "client",
    },
    {
        "email": "dale@printy.ke",
        "name": "Dale Carnegie",
        "role": "partner",
    },
    {
        "email": "jon@northpress.co.ke",
        "name": "Jon Weber",
        "role": "production",
    },
    {
        "email": "admin@printy.ke",
        "name": "Printy Operations",
        "role": "super_admin",
    },
]


class Command(BaseCommand):
    help = "Create the printy_ui prototype demo accounts (verified, password 'printy')."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="printy",
            help="Password assigned to demo accounts that do not already exist.",
        )

    def handle(self, *args, **options):
        from allauth.account.models import EmailAddress

        User = get_user_model()
        password = options["password"]
        created_count = 0
        existing_count = 0

        for spec in DEMO_ACCOUNTS:
            email = spec["email"].strip().lower()
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"name": spec["name"]},
            )
            if created:
                user.set_password(password)
            user.is_active = True
            if spec["role"] == "super_admin":
                user.is_staff = True
                user.is_superuser = True
            user.save()

            EmailAddress.objects.update_or_create(
                user=user,
                email=email,
                defaults={"primary": True, "verified": True},
            )

            assign_role(user, spec["role"], source="seed_demo_accounts")

            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"created {email} ({spec['role']})")
                )
            else:
                existing_count += 1
                self.stdout.write(
                    f"already exists {email} (role synced, password unchanged)"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {created_count} demo account(s) created, "
                f"{existing_count} already existed."
            )
        )