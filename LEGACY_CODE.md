# Legacy / Alternate Code

The main project uses **`config/settings.py`** (see `manage.py` and CI).

The following folders contain **legacy or alternate** Django project structures and are **not used** by the main application:

- **`printy_api/src/`** — Alternate project layout with different app structure
- **`printy_api/printy_api/`** — Minimal shops-only project layout

These may be remnants from earlier development or separate experiments. The canonical models, views, and URLs live in the top-level apps (`quotes`, `shops`, `production`, etc.) under `config/` settings.

**Recommendation:** Remove these folders if no longer needed, or migrate any unique logic into the main codebase first.
