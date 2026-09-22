# Print sides / colour mode — label-space contract

**TL;DR:** the friendly label space (`double`, `single`, `full_color`,
`black_only`) is **input-only**. It must always be normalized to the canonical
codes (`SIMPLEX`/`DUPLEX`, `BW`/`COLOR`) before it touches a model row or is
matched against a rate card. Otherwise you get ghost errors like:

```
Print sides: "double" is not a valid choice.
Color mode: "full_color" is not a valid choice.
```

## The two vocabularies

| Concept          | Friendly (UI/API input)                     | Canonical (DB / rate card) |
| ---------------- | ------------------------------------------- | -------------------------- |
| Print sides      | `single`, `double`, `both`, `2`, `simplex`  | `SIMPLEX`, `DUPLEX`        |
| Colour mode      | `full_color`, `black_only`, `b&w`, `mono`   | `BW`, `COLOR`              |

Full alias maps: `COLOR_MODE_ALIASES` / `SIDES_ALIASES` in
`printy_api/pricing/choices.py`, resolved by `ColorMode.from_friendly()` and
`Sides.from_friendly()`.

## Rules (enforced by regression tests)

1. **Serializer entry points** use `PrintSidesField` / `ColorModeField` from
   `printy_api/api/spec_choice_fields.py`. These accept both vocabularies,
   store the canonical code, and still reject garbage (`"OCTOPUS"`, `"rgb"`).
   Do NOT reintroduce a plain `serializers.ChoiceField` for these keys.

2. **Model layer stores canonical only.** `QuoteItem.sides`, 
   `QuoteItem.color_mode`, `PrintingRate.color_mode`, etc. hold canonical codes.
   Any service that builds one of these from a raw calculator snapshot must
   canonicalize first. The quote-item builders use `_canonical_sides()` /
   `_canonical_color_mode()` from `quotes/services_workflow.py`.

3. **File paths currently normalizing:**
   - All calculator / shop-options / match-shops / client-calculator serializers
     (via `api/spec_choice_fields.py`).
   - `services/pricing/spec_normalization.py` (delegates to the enums).
   - `quotes/services_workflow.py` — `_build_quote_item()` and
     `_build_manager_intake_quote_item()`.
   - `jobs/views.py` — reorder-spec extraction.

## Why this error happened

The prefill endpoint advertises readable labels (`print.sides = "single"`).
The UI forwards those labels verbatim to the spec endpoints. Serializer fixes
made the API accept them, but the quote-item builders still wrote the raw
labels into `QuoteItem`, whose Django model `choices` are canonical — so the
labels re-surfaced on later validation/pricing as "not a valid choice".

## Tests to keep green

```powershell
# from printy_api, with DJANGO_SETTINGS_MODULE=config.test_settings
env\Scripts\python.exe -m pytest quotes\test_quote_item_label_normalization.py api\test_calculator_spec_harmonization.py api\test_production_matching.py
```

- `quotes/test_quote_item_label_normalization.py` — label space can never leak
  into `QuoteItem` (sides/colour canonicalized, garbage falls back to defaults,
  `full_clean()` always passes).
- `api/test_calculator_spec_harmonization.py` — every serializer boundary
  accepts friendly labels and still rejects junk.
- `api/test_production_matching.py` — prefill → shop-options round-trip works
  with `single`/`full_color` and prices shops.

## B&W is optional (not silently priced as colour)

`PrintingRate` already keyes rows by `color_mode` (`BW` vs `COLOR`), so a shop
that doesn't print black & white simply holds no `BW` row. `PrintingRate.resolve()`
only falls back to a machine's default rate when **both** `sheet_size` and
`color_mode` match, so a `black_only` spec on a COLOR-only shop resolves to
`(None, None)` → the shop is listed as "cannot produce" for that job, never
priced at colour rates. Add a `BW` `PrintingRate` row (e.g.
`seed_printer_full_color_duplex --include-bw`) to offer B&W.