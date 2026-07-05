"""MVP-safe admin dashboard payload."""

from accounts.models import User
from jobs.models import JobAssignment, JobFile, ManagedJob
from notifications.models import Notification
from payments.models import Payment
from quotes.models import CalculatorDraft, Quote, QuoteRequest
from shops.models import Shop


def build_admin_dashboard_payload(*, request=None):
    return {
        "counts": {
            "users": User.objects.count(),
            "shops": Shop.objects.count(),
            "calculator_drafts": CalculatorDraft.objects.count(),
            "quote_requests": QuoteRequest.objects.count(),
            "quotes": Quote.objects.count(),
            "managed_jobs": ManagedJob.objects.count(),
            "job_assignments": JobAssignment.objects.count(),
            "job_files": JobFile.objects.count(),
            "payments": Payment.objects.count(),
            "notifications": Notification.objects.count(),
        },
        "analytics": {
            "status": "postponed",
            "detail": "AnalyticsEvent is not part of the canonical MVP model set.",
        },
    }
