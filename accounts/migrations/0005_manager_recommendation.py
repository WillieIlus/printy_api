import uuid

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone

import accounts.validators


def populate_public_ids(apps, schema_editor):
    """Give every existing profile a stable public id and verification time.

    Existing verified managers predate ``manager_verified_at``; using their
    profile ``updated_at`` keeps the timestamp honest instead of pretending
    they were verified at deploy time.
    """
    UserProfile = apps.get_model("accounts", "UserProfile")
    now = timezone.now()
    for profile in UserProfile.objects.all().iterator():
        updates = {}
        if not profile.public_id:
            updates["public_id"] = uuid.uuid4()
        if profile.manager_verified and profile.manager_verified_at is None:
            updates["manager_verified_at"] = profile.updated_at or now
        if updates:
            UserProfile.objects.filter(pk=profile.pk).update(**updates)


def clear_public_ids(apps, schema_editor):
    """Reverse is a no-op: dropping the column removes the values anyway."""


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_userprofile_manager_public_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="public_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="public_display_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Name clients see. Falls back to the account name when empty.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="manager_photo",
            field=models.ImageField(
                blank=True,
                help_text="Validated JPEG/PNG/WebP portrait shown on the public recommendation card.",
                null=True,
                upload_to="manager_photos/",
                validators=[accounts.validators.validate_manager_photo],
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="manager_verified_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When staff last verified this manager. Set and cleared by the admin verification toggle.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="accepts_new_jobs",
            field=models.BooleanField(
                default=True,
                help_text="A manager can pause new-job recommendations without losing verified status.",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="max_concurrent_jobs",
            field=models.PositiveSmallIntegerField(
                default=3,
                help_text="Active-job capacity. A manager at this load is excluded from recommendations.",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="last_assigned_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When a managed job was last assigned to this manager. Used for fair rotation.",
                null=True,
            ),
        ),
        migrations.RunPython(populate_public_ids, clear_public_ids),
        migrations.AlterField(
            model_name="userprofile",
            name="public_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text="Stable public identifier used by the recommendation and lock APIs.",
                unique=True,
            ),
        ),
        migrations.CreateModel(
            name="ManagerLock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, help_text="Timestamp when the record was created.", verbose_name="created at")),
                ("updated_at", models.DateTimeField(auto_now=True, help_text="Timestamp when the record was last updated.", verbose_name="updated at")),
                (
                    "buyer",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="manager_locks",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="buyer",
                    ),
                ),
                (
                    "manager",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="locked_by_buyers",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="manager",
                    ),
                ),
            ],
            options={
                "verbose_name": "Manager lock",
                "verbose_name_plural": "Manager locks",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("buyer", "manager"),
                        name="accounts_managerlock_unique_pair",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["buyer", "created_at"],
                        name="manager_lock_buyer_created_idx",
                    ),
                ],
            },
        ),
    ]
