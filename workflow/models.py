"""Models persisting the printy_workflow job graph.

Field names mirror printy_workflow/data/printy.ts interfaces. Where the TS
interface stores a nested literal (specs, owner, dispute, history, feed) Django
stores it as JSON so the serialized contract matches the frontend types 1:1.

Every value in DB matches the TS source of truth; see workflow/seed.py.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel

from .choices import (
    WorkflowCustody,
    WorkflowPressState,
    WorkflowStage,
    WorkflowStatus,
)


class WorkflowManager(TimeStampedModel):
    """Mirrors the `Manager` interface in printy_workflow."""

    id = models.CharField(max_length=32, primary_key=True, verbose_name=_("key"))
    name = models.CharField(max_length=255)
    initials = models.CharField(max_length=8)
    tag = models.CharField(max_length=255, blank=True, default="")
    on_time = models.PositiveSmallIntegerField(default=0, verbose_name=_("on-time percent"))
    hue = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["name"]
        verbose_name = _("workflow manager")
        verbose_name_plural = _("workflow managers")

    def __str__(self) -> str:
        return self.name


class WorkflowPrinter(TimeStampedModel):
    """Mirrors the `Printer` interface in printy_workflow."""

    id = models.CharField(max_length=32, primary_key=True, verbose_name=_("key"))
    name = models.CharField(max_length=255)
    contact = models.CharField(max_length=255)
    city = models.CharField(max_length=255)
    caps = models.JSONField(default=list, verbose_name=_("capabilities"))
    verified = models.BooleanField(default=False)
    rating = models.FloatField(default=0)
    jobs_done = models.PositiveIntegerField(default=0)
    on_time = models.PositiveSmallIntegerField(default=0, verbose_name=_("on-time percent"))
    initials = models.CharField(max_length=8)
    hue = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["name"]
        verbose_name = _("workflow printer")
        verbose_name_plural = _("workflow printers")

    def __str__(self) -> str:
        return self.name


class WorkflowJob(TimeStampedModel):
    """Mirrors the `Job` interface in printy_workflow.

    `owner` (ball holder) and `specs`/`dispute`/`history`/`feed` are stored as
    JSON matching the TS properties {name, role, action, waitingHrs, slaHrs},
    {material, colors, finish, size}, {reason, openedBy, at, amount, resolved},
    StageEvent[] and FeedEntry[] respectively.
    """

    id = models.CharField(max_length=32, primary_key=True, verbose_name=_("key"))
    code = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    qty = models.PositiveIntegerField()
    value = models.DecimalField(max_digits=14, decimal_places=2)

    buyer_id = models.CharField(max_length=32)
    buyer_name = models.CharField(max_length=255)
    buyer_company = models.CharField(max_length=255)

    manager = models.ForeignKey(
        WorkflowManager,
        on_delete=models.PROTECT,
        related_name="jobs",
    )
    printer = models.ForeignKey(
        WorkflowPrinter,
        on_delete=models.PROTECT,
        related_name="jobs",
        null=True,
        blank=True,
    )

    specs = models.JSONField(default=dict, verbose_name=_("specification"))
    proof_img = models.CharField(max_length=255, blank=True, default="", verbose_name=_("proof image"))

    status = models.CharField(
        max_length=16,
        choices=WorkflowStatus.choices,
        default=WorkflowStatus.ON_TRACK,
    )
    custody = models.CharField(
        max_length=16,
        choices=WorkflowCustody.choices,
        default=WorkflowCustody.AWAITING,
    )
    stage = models.CharField(
        max_length=16,
        choices=WorkflowStage.choices,
        default=WorkflowStage.QUOTE,
    )
    press = models.CharField(
        max_length=16,
        choices=WorkflowPressState.choices,
        null=True,
        blank=True,
    )
    progress = models.PositiveSmallIntegerField(null=True, blank=True)

    owner = models.JSONField(default=dict, verbose_name=_("ball holder"))
    eta = models.CharField(max_length=64, blank=True, default="")
    placed_at = models.CharField(max_length=64, blank=True, default="")

    dispute = models.JSONField(null=True, blank=True)
    history = models.JSONField(default=list, verbose_name=_("stage events"))
    feed = models.JSONField(default=list, verbose_name=_("activity feed"))

    class Meta:
        ordering = ["-placed_at"]
        verbose_name = _("workflow job")
        verbose_name_plural = _("workflow jobs")

    def __str__(self) -> str:
        return f"{self.code} - {self.title}"