from __future__ import annotations

from services.engine.schemas.inputs import FinishingSpec, JobSpec
from services.engine.schemas.results import (
    BookletLayoutResult,
    FinishingPlanResult,
    FlatSheetLayoutResult,
    RollLayoutResult,
)


class FinishingPlanner:
    def plan(
        self,
        job: JobSpec,
        finishing: FinishingSpec | None,
        layout_result: FlatSheetLayoutResult | RollLayoutResult | BookletLayoutResult | None,
    ) -> FinishingPlanResult:
        if finishing is None or layout_result is None:
            return FinishingPlanResult()

        lamination_units = self._lamination_units(job, finishing, layout_result)
        cut_units, estimated_cuts = self._cutting_units(layout_result, finishing)
        fold_units = job.quantity * max(finishing.folding_lines, 0)
        crease_units = job.quantity * max(finishing.crease_lines, 0)
        stitch_units = job.quantity if finishing.stitched and isinstance(layout_result, BookletLayoutResult) else 0
        eyelet_units = job.quantity * max(finishing.eyelets, 0)
        notes: list[str] = []
        if finishing.hems:
            notes.append("Hem allowance should be added in banner material costing.")
        if finishing.welds:
            notes.append("Weld allowance should be added in banner material costing.")

        return FinishingPlanResult(
            lamination_units=lamination_units,
            cut_units=cut_units,
            fold_units=fold_units,
            crease_units=crease_units,
            stitch_units=stitch_units,
            eyelet_units=eyelet_units,
            estimated_cut_passes=estimated_cuts,
            extra_finish_notes=notes,
        )

    @staticmethod
    def _lamination_units(
        job: JobSpec,
        finishing: FinishingSpec,
        layout_result: FlatSheetLayoutResult | RollLayoutResult | BookletLayoutResult,
    ) -> int:
        side_count = max(finishing.lamination_sides, 0)
        if side_count <= 0:
            return 0
        if finishing.lamination_mode == "per_piece":
            return job.quantity * side_count
        if isinstance(layout_result, FlatSheetLayoutResult):
            return layout_result.total_sheets * side_count
        if isinstance(layout_result, BookletLayoutResult):
            return layout_result.total_sheet_count * side_count
        if isinstance(layout_result, RollLayoutResult):
            return job.quantity * side_count
        return 0

    @staticmethod
    def _cutting_units(
        layout_result: FlatSheetLayoutResult | RollLayoutResult | BookletLayoutResult,
        finishing: FinishingSpec,
    ) -> tuple[int, int]:
        if not finishing.cutting_mode:
            return 0, 0
        if isinstance(layout_result, FlatSheetLayoutResult):
            estimated = max(layout_result.items_across - 1, 0) + max(layout_result.items_down - 1, 0)
            if finishing.cutting_mode == "per_sheet":
                return layout_result.total_sheets, estimated
            return 1, estimated
        if isinstance(layout_result, RollLayoutResult):
            estimated = max(layout_result.items_across - 1, 0)
            if finishing.cutting_mode == "per_piece":
                return max(layout_result.total_tile_instances, 0), estimated
            return 1, estimated
        if isinstance(layout_result, BookletLayoutResult):
            estimated = 1 if layout_result.total_sheet_count else 0
            return 1, estimated
        return 0, 0

