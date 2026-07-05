"""MVP placeholders for postponed calculator draft file grouping."""


def _postponed():
    raise ValueError("CalculatorDraftFile is postponed for MVP; use CalculatorDraft and JobFile.")


def sync_quote_request_from_file(draft_file, draft):
    return draft


def ensure_calculator_draft_file(*args, **kwargs):
    _postponed()


def ensure_calculator_draft_file_for_request(*args, **kwargs):
    _postponed()


def build_calculator_draft_file_payload(*args, **kwargs):
    _postponed()


def build_dashboard_quote_file_payload(*args, **kwargs):
    _postponed()


def build_calculator_draft_group_payload(*args, **kwargs):
    _postponed()
