from __future__ import annotations

from services.engine.schemas.inputs import JobSpec, MediaSpec
from services.engine.schemas.results import MediaFitResult
from services.engine.utils.geometry import area, fit_count, occupied_span
from services.engine.utils.rounding import round_mm, round_ratio


class MediaFitService:
    """Shared fit/orientation/media-selection logic."""

    def piece_dimensions(self, job: JobSpec, rotated: bool = False) -> tuple[float, float]:
        width = float(job.finished_width_mm) + (float(job.bleed_mm) * 2)
        height = float(job.finished_height_mm) + (float(job.bleed_mm) * 2)
        if rotated:
            return height, width
        return width, height

    def sheet_fit(self, media: MediaSpec, job: JobSpec) -> MediaFitResult:
        if media.is_roll:
            return MediaFitResult(
                media=media,
                fits=False,
                rotated=False,
                item_width_mm=0,
                item_height_mm=0,
                printable_width_mm=media.printable_width_mm,
                printable_height_mm=media.printable_height_mm,
            )

        options = [False, True] if job.allow_rotation else [False]
        best: MediaFitResult | None = None
        for rotated in options:
            item_width, item_height = self.piece_dimensions(job, rotated=rotated)
            printable_width = media.printable_width_mm
            printable_height = media.printable_height_mm or 0
            across = fit_count(printable_width, item_width, float(job.gap_mm))
            down = fit_count(printable_height, item_height, float(job.gap_mm))
            copies = across * down
            occupied_width = occupied_span(across, item_width, float(job.gap_mm))
            occupied_height = occupied_span(down, item_height, float(job.gap_mm))
            occupied_area = area(occupied_width, occupied_height)
            printable_area = area(printable_width, printable_height)
            waste = max(printable_area - occupied_area, 0.0)
            utilization = (occupied_area / printable_area) if printable_area else 0.0
            candidate = MediaFitResult(
                media=media,
                fits=copies > 0,
                rotated=rotated,
                item_width_mm=round_mm(item_width),
                item_height_mm=round_mm(item_height),
                printable_width_mm=round_mm(printable_width),
                printable_height_mm=round_mm(printable_height),
                items_across=across,
                items_down=down,
                copies_per_sheet=copies,
                waste_area_mm2=round_mm(waste),
                utilization_ratio=round_ratio(utilization),
            )
            if best is None or self._sheet_score(candidate) > self._sheet_score(best):
                best = candidate

        return best or MediaFitResult(
            media=media,
            fits=False,
            rotated=False,
            item_width_mm=0,
            item_height_mm=0,
            printable_width_mm=media.printable_width_mm,
            printable_height_mm=media.printable_height_mm,
        )

    def choose_best_sheet(self, media_options: list[MediaSpec], job: JobSpec) -> MediaFitResult | None:
        valid_results = [self.sheet_fit(media, job) for media in media_options if not media.is_roll]
        valid_results = [result for result in valid_results if result.fits]
        if not valid_results:
            return None
        return max(valid_results, key=self._sheet_score)

    def roll_orientation(self, media: MediaSpec, job: JobSpec) -> MediaFitResult:
        options = [False, True] if job.allow_rotation else [False]
        best: MediaFitResult | None = None
        for rotated in options:
            item_width, item_height = self.piece_dimensions(job, rotated=rotated)
            printable_width = media.printable_width_mm
            fits = item_width <= printable_width
            across = fit_count(printable_width, item_width, float(job.gap_mm)) if fits else 0
            utilization = (
                occupied_span(across, item_width, float(job.gap_mm)) / printable_width
            ) if printable_width else 0.0
            candidate = MediaFitResult(
                media=media,
                fits=fits,
                rotated=rotated,
                item_width_mm=round_mm(item_width),
                item_height_mm=round_mm(item_height),
                printable_width_mm=round_mm(printable_width),
                printable_height_mm=media.printable_height_mm,
                items_across=across,
                items_down=0,
                copies_per_sheet=across,
                waste_area_mm2=0,
                utilization_ratio=round_ratio(utilization),
            )
            if best is None or self._roll_score(candidate) > self._roll_score(best):
                best = candidate
        return best or MediaFitResult(
            media=media,
            fits=False,
            rotated=False,
            item_width_mm=0,
            item_height_mm=0,
            printable_width_mm=media.printable_width_mm,
            printable_height_mm=media.printable_height_mm,
        )

    @staticmethod
    def _sheet_score(result: MediaFitResult) -> tuple[int, float, float]:
        return (
            int(result.copies_per_sheet),
            float(result.utilization_ratio),
            -float(result.waste_area_mm2),
        )

    @staticmethod
    def _roll_score(result: MediaFitResult) -> tuple[int, float, float]:
        return (
            int(result.fits),
            int(result.items_across),
            float(result.utilization_ratio),
        )
