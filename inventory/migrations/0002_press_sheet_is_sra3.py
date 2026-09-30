"""Run every sheet-fed stock on the SRA3 press sheet.

The digital presses are configured for SRA3 (320 x 450mm) and the default
printing rate is the SRA3 rate, but some paper rows were still recorded against
pre-cut A3/A4 stock. Because the imposition is built from the matched paper's
own geometry, those rows made the engine impose finished pieces onto A3/A4
instead of the press sheet: an A5 flyer came out at 2 copies per A3 sheet and
A4 letterhead at 1 copy per sheet, against 16 up on SRA3.

A3/A4 are finished/trim sizes here, never a press sheet. This re-points that
stock at the shop's SRA3 press sheet, consolidating into the SRA3 row a shop
already holds for the same gsm and paper type, and deactivating the pre-cut row
it replaces so it can never be matched again.
"""

from django.db import migrations

SRA3_WIDTH_MM = 320
SRA3_HEIGHT_MM = 450

# The sheet sizes that are finished/trim sizes here and never a press sheet.
_TRIM_SHEET_SIZES = ("A4", "A3")

# Fields that describe the physical sheet of stock being sold. They move to the
# SRA3 row; identity fields (name, category, stock flags) stay with the row that
# already holds that gsm/paper-type combination.
_STOCK_FIELDS = (
    "buying_price",
    "selling_price",
    "quantity_in_stock",
    "reorder_level",
)


def _leading_sheet_size(value):
    """The trim sheet size a stock label leads with, or '' if it leads with none."""
    label = (value or "").strip()
    for size in _TRIM_SHEET_SIZES:
        if label.lower().startswith(f"{size.lower()} "):
            return size
    return ""


def _sra3_name(paper, old_sheet_size):
    """The stock name with its leading sheet-size token corrected to SRA3.

    Stops the buyer-facing label reading "A3 200g Matt" on an SRA3 sheet. Only a
    leading sheet-size token is rewritten, so any other naming is left alone.
    """
    if not old_sheet_size:
        return None
    name = (paper.name or "").strip()
    if not name.lower().startswith(f"{old_sheet_size.lower()} "):
        return None
    return f"SRA3 {name[len(old_sheet_size) + 1:]}"


def _rename(target, name):
    """Apply a corrected stock name to both name and display_name.

    The live model derives display_name from name inside Paper.save(), but a
    migration's historical model has no custom save(), so both are written here.
    """
    if not name:
        return
    target.name = name
    target.display_name = name


def _repoint_papers_to_sra3(apps, schema_editor):
    Paper = apps.get_model("inventory", "Paper")

    legacy = Paper.objects.exclude(sheet_size="SRA3").order_by("shop_id", "gsm", "id")
    for paper in legacy:
        old_sheet_size = paper.sheet_size
        corrected_name = _sra3_name(paper, old_sheet_size)
        sra3 = Paper.objects.filter(
            shop_id=paper.shop_id,
            sheet_size="SRA3",
            gsm=paper.gsm,
            paper_type=paper.paper_type,
        ).first()

        if sra3 is None:
            paper.sheet_size = "SRA3"
            paper.width_mm = SRA3_WIDTH_MM
            paper.height_mm = SRA3_HEIGHT_MM
            _rename(paper, corrected_name)
            paper.save()
            continue

        if paper.is_default:
            # Hand the default flag over first: only one default paper is
            # allowed per shop, so the target has to be saved after this.
            paper.is_default = False
            paper.save(update_fields=["is_default"])

        for field in _STOCK_FIELDS:
            setattr(sra3, field, getattr(paper, field))
        _rename(sra3, corrected_name)
        sra3.is_default = paper.is_default or sra3.is_default
        sra3.is_active = True
        sra3.save()

        # The pre-cut row is superseded. It stays as a record but can no longer
        # be picked as the sheet the job is imposed onto.
        paper.is_active = False
        paper.save(update_fields=["is_active"])

    # Sweep any SRA3 stock still labelled with another press size, so the label
    # always matches the sheet the job is actually imposed onto.
    for paper in Paper.objects.filter(sheet_size="SRA3").order_by("shop_id", "gsm", "id"):
        for label_field in ("name", "display_name"):
            corrected_name = _sra3_name(paper, _leading_sheet_size(getattr(paper, label_field)))
            if corrected_name:
                _rename(paper, corrected_name)
                paper.save()
                break


def _noop_reverse(apps, schema_editor):
    """Geometry is not recoverable per row; re-running forward is the fix."""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(_repoint_papers_to_sra3, _noop_reverse),
    ]
