"""
Large-format roll-media pricing calculator.

Pure calculation module — no ORM saves, no HTTP, no side effects.
Called by quotes/pricing_service.py::_compute_large_format_pricing().

Upgrade over the previous area-only formula:
  - bleed expansion (via MediaFitService inside RollLayoutImposer)
  - auto-rotation to minimise roll consumption
  - width-based nesting (items_across)
  - consumed roll length (rows × item_height + lead-in + lead-out margins)
  - billable media area  = roll_length × printable_width  (not artwork area)
  - minimum charge / min area enforcement from Product.min_area_m2
  - oversized-job tiling with tile count and overlap-ready structure
  - transparent breakdown ready for pricing_snapshot

Backward-compat guarantee:
  When Material.production_size is None (no roll width configured), the calculator
  falls back to raw artwork area pricing — identical to the previous formula.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from services.engine.integration import build_media_spec_from_material
from services.engine.schemas.inputs import JobSpec
from services.engine.services.roll_layout_imposer import RollLayoutImposer


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------

@dataclass
class LargeFormatCalcResult:
    # Layout provenance
    orientation: str            # "portrait" | "landscape"
    rotated: bool
    items_across: int           # nesting: pieces side-by-side across roll width
    rows: int                   # total print rows for all copies
    tiled: bool                 # True when piece exceeds roll width and is tiled
    tile_count: int             # panels per piece (tiles_x * tiles_y); 1 when not tiled

    # Roll consumption (mm)
    consumed_length_mm: Decimal # roll_length_mm from imposer, including lead-in/out margins

    # Areas — all Decimal, m²
    billable_media_area_m2: Decimal  # consumed_length × printable_width / 1_000_000
    artwork_area_m2: Decimal         # (w/1000) × (h/1000) × qty — finished-size only
    waste_area_m2: Decimal           # billable_media_area - artwork_area (≥ 0)

    # Cost components — all Decimal, shop currency
    material_cost: Decimal
    print_cost: Decimal              # from material.print_price_per_sqm
    finishing_cost: Decimal          # pre-computed by caller; stored for snapshot
    final_price: Decimal             # material + print + finishing + services

    # Flags
    minimum_charge_applied: bool
    fallback_artwork_area: bool      # True when production_size is None

    # Narrative
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------

def calculate_large_format(
    *,
    width_mm: int,
    height_mm: int,
    quantity: int,
    material,               # pricing.models.Material ORM instance
    product,                # catalog.models.Product ORM instance
    finishing_total: Decimal,
    services_total: Decimal,
) -> LargeFormatCalcResult:
    """
    Compute large-format roll-media pricing.

    finishing_total and services_total must be pre-computed by the caller so
    this function remains pure (no queryset access) and independently testable.
    """
    _D = _decimal

    # ------------------------------------------------------------------
    # 1. Artwork area — baseline for fallback and waste comparison
    # ------------------------------------------------------------------
    artwork_area = (
        Decimal(str(width_mm)) / 1000
        * Decimal(str(height_mm)) / 1000
        * Decimal(str(quantity))
    )

    # ------------------------------------------------------------------
    # 2. Attempt roll layout via production_size
    # ------------------------------------------------------------------
    media_spec = build_media_spec_from_material(material)

    if media_spec is None:
        # No roll width configured — fall back to pure artwork area
        return _fallback_result(
            artwork_area=artwork_area,
            material=material,
            finishing_total=finishing_total,
            services_total=services_total,
            product=product,
            quantity=quantity,
        )

    # ------------------------------------------------------------------
    # 3. Build JobSpec and impose onto roll
    #    MediaFitService.piece_dimensions() adds bleed_mm * 2 per axis
    # ------------------------------------------------------------------
    bleed_mm = float(getattr(product, "default_bleed_mm", 0) or 0)
    job_spec = JobSpec(
        product_type=getattr(product, "name", "large_format") or "large_format",
        finished_width_mm=float(width_mm),
        finished_height_mm=float(height_mm),
        quantity=quantity,
        bleed_mm=bleed_mm,
        gap_mm=0.0,
        allow_rotation=True,
        roll_overlap_mm=0.0,
    )
    layout = RollLayoutImposer().impose(job_spec, media_spec)

    # ------------------------------------------------------------------
    # 4. Billable media area = full roll width × consumed length
    #    Use Decimal(str(...)) to avoid float-precision contamination
    # ------------------------------------------------------------------
    consumed_length = Decimal(str(layout.roll_length_mm))
    printable_width = Decimal(str(layout.printable_width_mm))
    billable_area = (consumed_length * printable_width / Decimal("1000000")).quantize(Decimal("0.0001"))

    # ------------------------------------------------------------------
    # 5. Orientation and tiling warnings
    # ------------------------------------------------------------------
    orientation = "landscape" if layout.rotated else "portrait"
    warnings: list[str] = list(layout.notes or [])  # imposer notes include tiling detail

    # ------------------------------------------------------------------
    # 6. Minimum area check (Product.min_area_m2)
    # ------------------------------------------------------------------
    min_area = _D(getattr(product, "min_area_m2", None))
    rate_area = billable_area
    minimum_charge_applied = False
    min_area_adjustment = Decimal("0")

    if min_area > 0 and rate_area < min_area:
        rate_area = min_area
        minimum_charge_applied = True
        warnings.append(
            f"Minimum charge area of {min_area} m\u00b2 applied "
            f"(artwork area {billable_area.quantize(Decimal('0.0001'))} m\u00b2 is below threshold)."
        )

    # ------------------------------------------------------------------
    # 7. Cost lines
    # ------------------------------------------------------------------
    mat_rate = _D(material.selling_price)
    prn_rate = _D(getattr(material, "print_price_per_sqm", None))
    mat_cost = (mat_rate * rate_area).quantize(Decimal("0.01"))
    prn_cost = (prn_rate * rate_area).quantize(Decimal("0.01"))
    final_price = (mat_cost + prn_cost + finishing_total + services_total).quantize(Decimal("0.01"))

    # ------------------------------------------------------------------
    # 8. Waste
    # ------------------------------------------------------------------
    waste = max(billable_area - artwork_area.quantize(Decimal("0.0001")), Decimal("0")).quantize(Decimal("0.0001"))

    return LargeFormatCalcResult(
        orientation=orientation,
        rotated=layout.rotated,
        items_across=layout.items_across,
        rows=layout.total_rows,
        tiled=layout.needs_tiling,
        tile_count=layout.total_tiles,
        consumed_length_mm=consumed_length.quantize(Decimal("0.01")),
        billable_media_area_m2=billable_area,
        artwork_area_m2=artwork_area.quantize(Decimal("0.0001")),
        waste_area_m2=waste,
        material_cost=mat_cost,
        print_cost=prn_cost,
        finishing_cost=finishing_total.quantize(Decimal("0.01")),
        final_price=final_price,
        minimum_charge_applied=minimum_charge_applied,
        fallback_artwork_area=False,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Snapshot builder — produces the dict stored in pricing_snapshot.breakdown
# ---------------------------------------------------------------------------

def build_large_format_snapshot(calc: LargeFormatCalcResult) -> dict:
    """
    Return the structured breakdown dict for pricing_snapshot.
    All required response keys are present regardless of fallback mode.
    """
    return {
        # Layout
        "orientation": calc.orientation,
        "rotated": calc.rotated,
        "items_across": calc.items_across,
        "rows": calc.rows,
        "tiled": calc.tiled,
        "tile_count": calc.tile_count,
        # Roll consumption (None in fallback — no roll width)
        "consumed_length_mm": (
            int(calc.consumed_length_mm) if not calc.fallback_artwork_area else None
        ),
        # Areas
        "billable_media_area_m2": str(calc.billable_media_area_m2),
        "artwork_area_m2": str(calc.artwork_area_m2),
        "waste_area_m2": str(calc.waste_area_m2),
        # Costs
        "material_cost": str(calc.material_cost),
        "print_cost": str(calc.print_cost),
        "finishing_cost": str(calc.finishing_cost),
        # Flags
        "minimum_charge_applied": calc.minimum_charge_applied,
        "fallback_artwork_area": calc.fallback_artwork_area,
        # Final
        "final_price": str(calc.final_price),
        # Warnings list for UX
        "warnings": calc.warnings,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _decimal(value, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _fallback_result(
    *,
    artwork_area: Decimal,
    material,
    finishing_total: Decimal,
    services_total: Decimal,
    product,
    quantity: int,
) -> LargeFormatCalcResult:
    """
    Area-only pricing — used when material has no production_size (no roll width).
    Replicates the original formula: selling_price * artwork_area.
    Existing tests with un-configured materials must continue to pass.
    """
    mat_rate = _decimal(material.selling_price)
    prn_rate = _decimal(getattr(material, "print_price_per_sqm", None))

    # Min area still applies in fallback mode
    min_area = _decimal(getattr(product, "min_area_m2", None))
    rate_area = artwork_area.quantize(Decimal("0.0001"))
    minimum_charge_applied = False
    warnings: list[str] = [
        "No roll width configured for this material — area-only pricing used."
    ]

    if min_area > 0 and rate_area < min_area:
        rate_area = min_area
        minimum_charge_applied = True
        warnings.append(
            f"Minimum charge area of {min_area} m\u00b2 applied."
        )

    mat_cost = (mat_rate * rate_area).quantize(Decimal("0.01"))
    prn_cost = (prn_rate * rate_area).quantize(Decimal("0.01"))
    final_price = (mat_cost + prn_cost + finishing_total + services_total).quantize(Decimal("0.01"))
    artwork_q = artwork_area.quantize(Decimal("0.0001"))

    return LargeFormatCalcResult(
        orientation="portrait",
        rotated=False,
        items_across=1,
        rows=quantity,
        tiled=False,
        tile_count=1,
        consumed_length_mm=Decimal("0"),
        billable_media_area_m2=rate_area,
        artwork_area_m2=artwork_q,
        waste_area_m2=Decimal("0"),
        material_cost=mat_cost,
        print_cost=prn_cost,
        finishing_cost=finishing_total.quantize(Decimal("0.01")),
        final_price=final_price,
        minimum_charge_applied=minimum_charge_applied,
        fallback_artwork_area=True,
        warnings=warnings,
    )
