from __future__ import annotations

from math import ceil

from services.engine.schemas.inputs import JobSpec, MediaSpec
from services.engine.schemas.results import FlatSheetLayoutResult
from services.engine.services.media_fit import MediaFitService


class FlatSheetImposer:
    def __init__(self, media_fit_service: MediaFitService | None = None) -> None:
        self.media_fit_service = media_fit_service or MediaFitService()

    def impose(self, job: JobSpec, media: MediaSpec) -> FlatSheetLayoutResult:
        fit = self.media_fit_service.sheet_fit(media, job)
        total_sheets = ceil(job.quantity / fit.copies_per_sheet) if fit.copies_per_sheet else 0
        notes: list[str] = []
        duplex_note = ""
        if job.sides > 1:
            duplex_note = "Duplex changes print pricing/impressions, not sheet fit."
            notes.append(duplex_note)

        return FlatSheetLayoutResult(
            fits=fit.fits,
            copies_per_sheet=fit.copies_per_sheet,
            chosen_orientation="rotated" if fit.rotated else "normal",
            sheet_width_mm=float(media.width_mm),
            sheet_height_mm=float(media.height_mm or 0),
            total_sheets=total_sheets,
            waste_area_mm2=fit.waste_area_mm2,
            utilization_ratio=fit.utilization_ratio,
            media_name=media.name,
            printable_width_mm=fit.printable_width_mm,
            printable_height_mm=float(fit.printable_height_mm or 0),
            items_across=fit.items_across,
            items_down=fit.items_down,
            duplex_note=duplex_note,
            notes=notes,
        )

    def impose_best(self, job: JobSpec, media_options: list[MediaSpec]) -> FlatSheetLayoutResult:
        best_fit = self.media_fit_service.choose_best_sheet(media_options, job)
        if best_fit is None or best_fit.media is None:
            return FlatSheetLayoutResult(
                fits=False,
                copies_per_sheet=0,
                chosen_orientation="normal",
                sheet_width_mm=0,
                sheet_height_mm=0,
                total_sheets=0,
                waste_area_mm2=0,
                utilization_ratio=0,
                notes=["No candidate flat sheet fits this job."],
            )
        return self.impose(job, best_fit.media)

