import logging

from quotes.models import QuoteFinancialSplit


logger = logging.getLogger(__name__)


def get_financial_split_for_job(managed_job):
    """
    Return the canonical financial split for a job.
    """
    if getattr(managed_job, "source_quote_id", None):
        split = QuoteFinancialSplit.objects.filter(quote_id=managed_job.source_quote_id).first()
        if split:
            return {
                "production_cost": split.production_cost,
                "shop_payout": split.shop_payout,
                "broker_payout": split.broker_payout,
                "printy_fee": split.printy_fee,
                "client_total": split.client_total,
                "source": "quote_financial_split",
            }
    return None
