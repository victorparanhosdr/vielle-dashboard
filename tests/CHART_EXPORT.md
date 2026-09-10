# Excel export checks

Install `requirements.txt` in the existing Python environment (adds openpyxl).
Run `python -m unittest discover -s tests -p 'test_*.py'`.

For a disposable local preview, run `python tests/preview_chart_export.py`.
Open http://127.0.0.1:8778 and sign in with `excel` / `ExcelLocal2026!`.
These credentials and fictitious records exist only in a temporary database.
All integration endpoints are blocked in this preview.

Run `node tests/ui_chart_export.cjs` with Playwright installed (or set
`PLAYWRIGHT_MODULE` to its installed path). It checks all eight downloads and
icon layout at 1440, 390 and 320 pixels. Screenshots go to `/tmp`.

The production route is `/api/export-chart?clinic=vielle&chart=revenue_daily&date_from=2026-09-01&date_to=2026-09-30`.
It requires a valid session, clinic membership, `dashboard.view` and
`dashboard.export`, independently of any supplied `view` parameter.

Each workbook contains chart summary, complete source records, original API
JSON (split into numbered parts only when required by Excel), and filter
context. It has no macros. Source text cannot become spreadsheet formulas.
The report query is rerun on the currently synchronized database, using the
filters from the last successfully rendered dashboard, not pending filters.
The existing booking date fallback is preserved and documented in Contexto.

No schema migration, database replacement, credentials change or deployment
is part of this change. The actual frontend files are under `static/`.
