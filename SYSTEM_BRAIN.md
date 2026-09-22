# Cerebro DOC4DOCS

Master: `/master/brain.html`. API: `GET /api/master/brain?clinic=vielle` and
`POST /api/master/brain/analyze?clinic=vielle` with `{"focus":"general"}`.
Both require an active Master session. POST additionally requires the existing
same-origin request guard. No public endpoint or new permission bypass exists.

## Data and persistence

Clinic databases are opened with SQLite `mode=ro` and `query_only`.
No initialization, migration, sync, copy, or deletion is performed in them.
Reads have a query deadline and a fixed allowlist. Patient identifiers, monetary
values, raw JSON, conversations, API keys and free-text sync messages are never
returned or sent to the AI. Sync timestamps/outcomes, record counts and date
extents are aggregated from the existing local tables.

Only analysis history is written: `DATA_DIR/system_brain.sqlite3`, table
`analyses`. Creation is lazy, on the first successful analysis. The latest 20
analyses per clinic are retained. There is no migration of existing databases.
Back up this file along with the existing persistent DATA_DIR volume if desired.

## Inventory and limitations

The map derives available modules from `access_policy.MODULES/clinic_modules`.
Files, hashes, local Python imports, function counts and literal API routes are
scanned from the installed source with AST. The fingerprint changes with source;
Railway's commit SHA identifies the release. New modules without a semantic
mapping are flagged, rather than inventing their data flow. Maintain
`MODULE_DETAILS` when introducing new semantic flows. The AST route inventory
does not claim to enumerate dynamically composed routes.

The interface refreshes once per minute while visible. It does not probe external
APIs or trigger synchronization. A successful historical sync is not a live API
health check. A 24-hour threshold is an attention heuristic, not a failure SLA.
Date extents do not prove that every intermediate record was synchronized.
Meta's existing last-sync marker has no failure history; the UI says so.

## AI

Read-only Chat Completions analysis on demand, no tools, no code execution and no
automatic repairs. Uses the clinic's existing OpenAI configuration, falling back
to Vielle when absent; the UI shows the configuration source. Optional
`SYSTEM_BRAIN_OPENAI_API_KEY` overrides the key for a dedicated installation.
`OPENAI_MODEL` retains the existing configured model (default gpt-4.1-mini).
The output is bounded and validated. Every finding must reference a supplied
evidence ID. Provider errors are replaced by safe messages. One analysis runs at
a time per process, with a 60-second per-Master interval and a 2000-token output
ceiling. Calls consume the configured API account; no background AI calls occur.

Official API reference: https://developers.openai.com/api/reference/resources/chat

## Verification

`python3 -m unittest discover -s tests -p 'test_system_brain.py' -v`

Tests use temporary SQLite databases and mocked OpenAI responses. They exercise
real HTTP guards, CSRF, throttling, data preservation, metadata minimization,
cross-clinic module availability and inventory updates. No patient database or
live integration is modified by the test suite.
