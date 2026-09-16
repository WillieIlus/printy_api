"""DRF serializers exposing the workflow exactly as printy_workflow types it.

Every field name matches the TS interfaces (Job, Manager, Printer) in
printy_workflow/data/printy.ts (camelCase, nullable keys nullable) so a
workflow store in printy_ui can hydrate directly from the API without a mapping
layer.
"""
from rest_framework import serializers

from .models import WorkflowJob, WorkflowManager, WorkflowPrinter


class WorkflowManagerSerializer(serializers.ModelSerializer):
    onTime = serializers.IntegerField(source="on_time")

    class Meta:
        model = WorkflowManager
        fields = ["id", "name", "initials", "tag", "onTime", "hue"]


class WorkflowPrinterSerializer(serializers.ModelSerializer):
    jobsDone = serializers.IntegerField(source="jobs_done")
    onTime = serializers.IntegerField(source="on_time")

    class Meta:
        model = WorkflowPrinter
        fields = ["id", "name", "contact", "city", "caps", "verified", "rating", "jobsDone", "onTime", "initials", "hue"]


class WorkflowJobSerializer(serializers.ModelSerializer):
    value = serializers.FloatField()
    buyerId = serializers.CharField(source="buyer_id")
    buyerName = serializers.CharField(source="buyer_name")
    buyerCompany = serializers.CharField(source="buyer_company")
    managerId = serializers.CharField(source="manager.id", read_only=True)
    printerId = serializers.CharField(source="printer.id", read_only=True, allow_null=True)
    specs = serializers.JSONField()
    proofImg = serializers.CharField(source="proof_img", allow_blank=True, allow_null=True)
    progress = serializers.IntegerField(allow_null=True)
    owner = serializers.JSONField()
    placedAt = serializers.CharField(source="placed_at")
    dispute = serializers.JSONField(allow_null=True)
    history = serializers.JSONField()
    feed = serializers.JSONField()

    class Meta:
        model = WorkflowJob
        fields = [
            "id",
            "code",
            "title",
            "product",
            "qty",
            "value",
            "buyerId",
            "buyerName",
            "buyerCompany",
            "managerId",
            "printerId",
            "specs",
            "proofImg",
            "status",
            "custody",
            "stage",
            "press",
            "progress",
            "owner",
            "eta",
            "placedAt",
            "dispute",
            "history",
            "feed",
        ]