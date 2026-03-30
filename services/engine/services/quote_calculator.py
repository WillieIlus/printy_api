from __future__ import annotations

from services.engine.schemas.inputs import FinishingSpec, JobSpec, MediaSpec
from services.engine.schemas.results import QuoteSummaryResult
from services.engine.services.booklet_imposer import BookletImposer
from services.engine.services.finishing_planner import FinishingPlanner
from services.engine.services.flat_sheet_imposer import FlatSheetImposer
from services.engine.services.media_fit import MediaFitService
from services.engine.services.roll_layout_imposer import RollLayoutImposer


class QuoteCalculator:
    def __init__(
        self,
        media_fit_service: MediaFitService | None = None,
        flat_sheet_imposer: FlatSheetImposer | None = None,
        roll_layout_imposer: RollLayoutImposer | None = None,
        booklet_imposer: BookletImposer | None = None,
        finishing_planner: FinishingPlanner | None = None,
    ) -> None:
        media_fit_service = media_fit_service or MediaFitService()
        self.flat_sheet_imposer = flat_sheet_imposer or FlatSheetImposer(media_fit_service)
        self.roll_layout_imposer = roll_layout_imposer or RollLayoutImposer(media_fit_service)
        self.booklet_imposer = booklet_imposer or BookletImposer()
        self.finishing_planner = finishing_planner or FinishingPlanner()

    def calculate(
        self,
        job: JobSpec,
        candidate_media: list[MediaSpec],
        finishing: FinishingSpec | None = None,
    ) -> QuoteSummaryResult:
        engine_type = self._resolve_engine(job, candidate_media)
        notes: list[str] = []

        if engine_type == "booklet":
            layout = self.booklet_imposer.impose(job)
            finishing_result = self.finishing_planner.plan(job, finishing, layout)
            notes.extend(layout.notes)
            notes.extend(finishing_result.extra_finish_notes)
            return QuoteSummaryResult(
                engine_type=engine_type,
                media_name=None,
                parent_sheets_required=layout.total_sheet_count,
                roll_length_required_mm=0,
                finishing=finishing_result,
                layout_result=layout,
                notes=notes,
            )

        if engine_type == "roll":
            layout = self.roll_layout_imposer.impose_best(job, candidate_media)
            finishing_result = self.finishing_planner.plan(job, finishing, layout)
            notes.extend(layout.notes)
            notes.extend(finishing_result.extra_finish_notes)
            return QuoteSummaryResult(
                engine_type=engine_type,
                media_name=layout.media_name,
                parent_sheets_required=0,
                roll_length_required_mm=layout.roll_length_mm,
                finishing=finishing_result,
                layout_result=layout,
                notes=notes,
            )

        layout = self.flat_sheet_imposer.impose_best(job, candidate_media)
        finishing_result = self.finishing_planner.plan(job, finishing, layout)
        notes.extend(layout.notes)
        notes.extend(finishing_result.extra_finish_notes)
        return QuoteSummaryResult(
            engine_type="flat_sheet",
            media_name=layout.media_name,
            parent_sheets_required=layout.total_sheets,
            roll_length_required_mm=0,
            finishing=finishing_result,
            layout_result=layout,
            notes=notes,
        )

    @staticmethod
    def _resolve_engine(job: JobSpec, candidate_media: list[MediaSpec]) -> str:
        product_type = (job.product_type or "").lower()
        if job.pages > 0 or "booklet" in product_type:
            return "booklet"

        roll_media = [media for media in candidate_media if media.is_roll]
        sheet_media = [media for media in candidate_media if not media.is_roll]
        roll_hint = any(token in product_type for token in ("banner", "roll", "large", "wide", "format"))

        if roll_media and (roll_hint or not sheet_media):
            return "roll"
        return "flat_sheet"
