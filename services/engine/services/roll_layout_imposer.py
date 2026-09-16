from __future__ import annotations

from math import ceil

from services.engine.schemas.inputs import JobSpec, MediaSpec
from services.engine.schemas.results import RollLayoutResult
from services.engine.services.media_fit import MediaFitService
from services.engine.utils.geometry import average_tile_size, fit_count, occupied_span, tiled_panel_count
from services.engine.utils.rounding import round_mm


class RollLayoutImposer:
    def __init__(self, media_fit_service: MediaFitService | None = None) -> None:
        self.media_fit_service = media_fit_service or MediaFitService()

    def impose(self, job: JobSpec, media: MediaSpec) -> RollLayoutResult:
        fit = self.media_fit_service.roll_orientation(media, job)
        printable_width = media.printable_width_mm
        if fit.fits and fit.items_across > 0:
            rows = ceil(job.quantity / fit.items_across)
            roll_length = (
                rows * fit.item_height_mm
                + max(rows - 1, 0) * float(job.gap_mm)
                + float(media.printable_margin_top_mm)
                + float(media.printable_margin_bottom_mm)
            )
            waste_width = max(
                printable_width - occupied_span(fit.items_across, fit.item_width_mm, float(job.gap_mm)),
                0.0,
            )
            return RollLayoutResult(
                fits_directly=True,
                rotated=fit.rotated,
                items_across=fit.items_across,
                total_rows=rows,
                roll_length_mm=round_mm(roll_length),
                needs_tiling=False,
                tiles_x=1,
                tiles_y=1,
                total_tiles=1,
                tile_width_mm=fit.item_width_mm,
                tile_height_mm=fit.item_height_mm,
                overlap_mm=float(job.roll_overlap_mm),
                media_name=media.name,
                printable_width_mm=round_mm(printable_width),
                waste_width_mm=round_mm(waste_width),
                total_tile_instances=job.quantity,
                notes=[],
            )
        return self._tile_job(job, media, fit.rotated)

    def impose_best(self, job: JobSpec, media_options: list[MediaSpec]) -> RollLayoutResult:
        roll_media = [media for media in media_options if media.is_roll]
        if not roll_media:
            return RollLayoutResult(
                fits_directly=False,
                rotated=False,
                items_across=0,
                total_rows=0,
                roll_length_mm=0,
                needs_tiling=False,
                tiles_x=0,
                tiles_y=0,
                total_tiles=0,
                tile_width_mm=0,
                tile_height_mm=0,
                overlap_mm=float(job.roll_overlap_mm),
                notes=["No candidate roll media provided."],
            )

        evaluated = [self.impose(job, media) for media in roll_media]
        direct_fit = [result for result in evaluated if result.fits_directly]
        if direct_fit:
            return min(direct_fit, key=lambda result: (result.roll_length_mm, -result.items_across))
        tiled = [result for result in evaluated if result.needs_tiling and result.total_tiles > 0]
        if tiled:
            return min(tiled, key=lambda result: (result.roll_length_mm, result.total_tiles))
        return evaluated[0]

    def _tile_job(self, job: JobSpec, media: MediaSpec, rotated: bool) -> RollLayoutResult:
        printable_width = media.printable_width_mm
        overlap = float(job.roll_overlap_mm)
        item_width, item_height = self.media_fit_service.piece_dimensions(job, rotated=rotated)
        max_tile_length = float(job.tile_max_length_mm or item_height)
        tiles_x = tiled_panel_count(item_width, printable_width, overlap)
        tiles_y = tiled_panel_count(item_height, max_tile_length, overlap)
        tile_width = average_tile_size(item_width, tiles_x, overlap) if tiles_x else 0.0
        tile_height = average_tile_size(item_height, tiles_y, overlap) if tiles_y else 0.0
        tile_items_across = fit_count(printable_width, tile_width, float(job.gap_mm)) if tile_width else 0
        total_tiles_per_piece = tiles_x * tiles_y
        total_tile_instances = total_tiles_per_piece * job.quantity
        rows = ceil(total_tile_instances / tile_items_across) if tile_items_across else 0
        roll_length = (
            rows * tile_height
            + max(rows - 1, 0) * float(job.gap_mm)
            + float(media.printable_margin_top_mm)
            + float(media.printable_margin_bottom_mm)
        ) if rows else 0.0
        waste_width = max(
            printable_width - occupied_span(tile_items_across, tile_width, float(job.gap_mm)),
            0.0,
        ) if tile_items_across else printable_width
        notes = [f"Tiled into {tiles_x} x {tiles_y} panel grid with {round_mm(overlap)} mm overlap."]
        if max_tile_length != item_height:
            notes.append(f"Tile max length limited to {round_mm(max_tile_length)} mm.")

        return RollLayoutResult(
            fits_directly=False,
            rotated=rotated,
            items_across=tile_items_across,
            total_rows=rows,
            roll_length_mm=round_mm(roll_length),
            needs_tiling=True,
            tiles_x=tiles_x,
            tiles_y=tiles_y,
            total_tiles=total_tiles_per_piece,
            tile_width_mm=round_mm(tile_width),
            tile_height_mm=round_mm(tile_height),
            overlap_mm=round_mm(overlap),
            media_name=media.name,
            printable_width_mm=round_mm(printable_width),
            waste_width_mm=round_mm(waste_width),
            total_tile_instances=total_tile_instances,
            notes=notes,
        )
