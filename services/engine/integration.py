from __future__ import annotations

from dataclasses import asdict, is_dataclass

from pricing.choices import ChargeUnit, Sides

from services.engine.schemas.inputs import FinishingSpec, JobSpec, MediaSpec


def build_media_spec_from_paper(paper) -> MediaSpec:
    width_mm, height_mm = paper.get_dimensions_mm() if hasattr(paper, "get_dimensions_mm") else (paper.width_mm, paper.height_mm)
    return MediaSpec(
        name=getattr(paper, "sheet_size", "") or getattr(paper, "name", "Sheet"),
        width_mm=float(width_mm or 0),
        height_mm=float(height_mm or 0),
        is_roll=False,
    )


def build_media_spec_from_material(material) -> MediaSpec | None:
    production_size = getattr(material, "production_size", None)
    if not production_size:
        return None
    return MediaSpec(
        name=getattr(production_size, "code", "") or getattr(material, "material_type", "Roll"),
        width_mm=float(getattr(production_size, "width_mm", 0) or 0),
        height_mm=None,
        is_roll=True,
    )


def build_job_spec(*, product=None, item=None, quantity: int | None = None, width_mm=None, height_mm=None, sides: str | None = None) -> JobSpec:
    resolved_product = product or getattr(item, "product", None)
    resolved_quantity = quantity if quantity is not None else getattr(item, "quantity", 0)
    resolved_width = width_mm if width_mm is not None else (
        getattr(item, "chosen_width_mm", None)
        or getattr(resolved_product, "default_finished_width_mm", 0)
    )
    resolved_height = height_mm if height_mm is not None else (
        getattr(item, "chosen_height_mm", None)
        or getattr(resolved_product, "default_finished_height_mm", 0)
    )
    resolved_sides = sides or getattr(item, "sides", None) or getattr(resolved_product, "default_sides", None) or Sides.SIMPLEX
    product_type = (
        getattr(resolved_product, "name", "")
        or getattr(item, "title", "")
        or getattr(item, "pricing_mode", "")
        or "print_job"
    )
    return JobSpec(
        product_type=product_type,
        finished_width_mm=float(resolved_width or 0),
        finished_height_mm=float(resolved_height or 0),
        quantity=int(resolved_quantity or 0),
        bleed_mm=float(getattr(resolved_product, "default_bleed_mm", 0) or 0),
        gap_mm=0,
        allow_rotation=True,
        sides=2 if resolved_sides == Sides.DUPLEX else 1,
    )


def classify_finishing_spec(finishing_objects, print_sides: str | None = None) -> FinishingSpec:
    lamination_sides = 0
    lamination_mode = None
    cutting_mode = None
    folding_lines = 0
    crease_lines = 0
    stitched = False
    eyelets = 0
    hems = False
    welds = False

    default_side_count = 2 if print_sides == Sides.DUPLEX else 1

    for obj in finishing_objects or []:
        rate = getattr(obj, "finishing_rate", obj)
        name = (getattr(rate, "name", "") or "").lower()
        slug = (getattr(rate, "slug", "") or "").lower()
        category_name = (getattr(getattr(rate, "category", None), "name", "") or "").lower()
        selected_side = getattr(obj, "selected_side", "both")
        apply_to_sides = getattr(obj, "apply_to_sides", "BOTH")
        side_count = _selected_side_count(selected_side, apply_to_sides, default_side_count)
        charge_unit = getattr(rate, "charge_unit", "")

        if _contains_any(name, slug, category_name, terms=("lamination", "laminate")):
            lamination_sides = max(lamination_sides, side_count)
            lamination_mode = "per_parent_sheet" if charge_unit in {ChargeUnit.PER_SHEET, ChargeUnit.PER_SIDE_PER_SHEET} else "per_piece"
        if _contains_any(name, slug, category_name, terms=("cut", "trim")):
            cutting_mode = "per_sheet" if charge_unit in {ChargeUnit.PER_SHEET, ChargeUnit.PER_SIDE_PER_SHEET} else "per_job"
        if _contains_any(name, slug, category_name, terms=("fold",)):
            folding_lines = max(folding_lines, 1)
        if _contains_any(name, slug, category_name, terms=("crease", "score")):
            crease_lines = max(crease_lines, 1)
        if _contains_any(name, slug, category_name, terms=("stitch", "staple", "saddle")):
            stitched = True
        if _contains_any(name, slug, category_name, terms=("eyelet", "grommet")):
            eyelets = max(eyelets, 1)
        if _contains_any(name, slug, category_name, terms=("hem",)):
            hems = True
        if _contains_any(name, slug, category_name, terms=("weld",)):
            welds = True

    return FinishingSpec(
        lamination_sides=lamination_sides,
        lamination_mode=lamination_mode,
        cutting_mode=cutting_mode,
        folding_lines=folding_lines,
        crease_lines=crease_lines,
        stitched=stitched,
        eyelets=eyelets,
        hems=hems,
        welds=welds,
    )


def serialize_result(value):
    if is_dataclass(value):
        return asdict(value)
    return value


def _selected_side_count(selected_side: str, apply_to_sides: str, default_side_count: int) -> int:
    if selected_side == "both" or apply_to_sides == "DOUBLE":
        return 2
    if selected_side in {"front", "back"} or apply_to_sides == "SINGLE":
        return 1
    return default_side_count


def _contains_any(*values: str, terms: tuple[str, ...]) -> bool:
    haystack = " ".join(values)
    return any(term in haystack for term in terms)
