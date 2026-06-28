from __future__ import annotations

from math import ceil

from services.engine.schemas.inputs import JobSpec
from services.engine.schemas.results import BookletLayoutResult, BookletSpread


class BookletImposer:
    def impose(self, job: JobSpec) -> BookletLayoutResult:
        if job.pages <= 0:
            return BookletLayoutResult(
                valid=False,
                adjusted_page_count=0,
                blanks_added=0,
                self_cover=True,
                cover_pages=0,
                inner_pages=0,
                sheets_per_booklet=0,
                cover_sheet_count=0,
                inner_sheet_count=0,
                total_sheet_count=0,
                notes=["Booklet jobs require a positive page count."],
            )

        adjusted_pages = self._normalize_pages(job.pages)
        blanks_added = adjusted_pages - job.pages
        self_cover = job.cover_pages <= 0
        cover_pages = 0 if self_cover else max(0, job.cover_pages)
        inner_pages = adjusted_pages if self_cover else max(0, adjusted_pages - cover_pages)
        sheets_per_booklet = adjusted_pages // 4
        cover_sheet_count = 0 if self_cover else ceil(cover_pages / 4) * job.quantity
        inner_sheet_count = sheets_per_booklet * job.quantity if self_cover else ceil(inner_pages / 4) * job.quantity
        notes: list[str] = []
        if blanks_added:
            notes.append(f"Added {blanks_added} blank page(s) to reach a multiple of 4.")
        if not self_cover:
            notes.append("Separate cover assumed for quoting.")

        return BookletLayoutResult(
            valid=True,
            adjusted_page_count=adjusted_pages,
            blanks_added=blanks_added,
            self_cover=self_cover,
            cover_pages=cover_pages,
            inner_pages=inner_pages,
            sheets_per_booklet=sheets_per_booklet,
            cover_sheet_count=cover_sheet_count,
            inner_sheet_count=inner_sheet_count,
            total_sheet_count=cover_sheet_count + inner_sheet_count,
            spread_map=self._build_spread_map(adjusted_pages),
            notes=notes,
        )

    @staticmethod
    def _normalize_pages(page_count: int) -> int:
        remainder = page_count % 4
        return page_count if remainder == 0 else page_count + (4 - remainder)

    @staticmethod
    def _build_spread_map(page_count: int) -> list[BookletSpread]:
        spreads: list[BookletSpread] = []
        sheet_count = page_count // 4
        for sheet_number in range(1, sheet_count + 1):
            left = page_count - ((sheet_number - 1) * 2)
            right = 1 + ((sheet_number - 1) * 2)
            inner_left = 2 + ((sheet_number - 1) * 2)
            inner_right = page_count - 1 - ((sheet_number - 1) * 2)
            spreads.append(
                BookletSpread(
                    sheet_number=sheet_number,
                    outer_front=(left, right),
                    outer_back=(inner_left, inner_right),
                    inner_front=(inner_right, inner_left),
                    inner_back=(right, left),
                )
            )
        return spreads

