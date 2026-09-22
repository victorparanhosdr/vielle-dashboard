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

The Master can select any of the latest 20 saved analyses for the selected clinic.
GET includes `analyses` newest first plus the backward-compatible `analysis` field.
Each response includes its database row ID; no user identifier is included.
New analyses also store aggregate dataset counts, enabling comparison with the
previous saved analysis. Existing payloads remain readable without a migration.

GET also includes `history_status` (`state`: `empty`, `ok`, `partial` or
`unavailable`; `skipped`: rejected rows in the latest 20, or null when unreadable).
Saved JSON is validated before reaching the UI, including nested findings,
evidence references and count snapshots. Extra fields are not forwarded and a
payload identifying another clinic is rejected. Legacy records without optional
metadata remain supported. Payload reads are bounded to 250,000 characters each.
Invalid records are omitted from that response only, with a visible warning;
they are not repaired, deleted or rewritten during reads. Missing/incompatible
tables, locked databases or invalid SQLite files do not break the map or current
diagnostic. Comparisons use the previous readable analysis, which may not be the
immediately preceding saved row. The JSON report retains these history limits.

An unavailable history pauses new AI requests in both the UI and backend, before
calling the provider. Refreshing checks availability again. A missing history
file is a normal first-run state, not a failure. This read check does not guarantee
that a later write will succeed; normal save errors still follow the existing
server error handling. No automatic repair or schema migration is performed.

Comparisons run locally in `static/master/brain-data.js` and do not call OpenAI.
State/count changes are observations, not proof of a fix, deletion or new record.
Missing measurements are not converted to zero or interpreted as resolved errors.
Older analyses without count snapshots explicitly omit that part of the comparison.

New analyses retain the server-owned `inventory_status` (`complete` or `partial`),
also sent as technical context to the model and included in JSON exports. Legacy
analyses remain readable without it and are never rewritten. Code equality/change
is stated only when both inventories were complete and both fingerprints exist;
otherwise that comparison is explicitly unavailable. Other measured differences
can still be compared when observation timestamps are valid and ordered. Missing,
zero or reversed observation times suppress chronological comparison rather than
implying an ordered period. The saved analysis itself remains visible.

The download icon exports a JSON technical report with the current diagnostic and
the selected saved analysis, each retaining its own observation timestamp. OpenAI
configuration, raw logs, actor IDs and unrelated response fields are excluded.

## Inventory and limitations

The map derives available modules from `access_policy.MODULES/clinic_modules`.
Files and hashes are inventoried from the installed source. Local Python imports,
Python function counts and literal API routes are scanned with AST. The fingerprint changes with source;
Railway's commit SHA identifies the release. New modules without a semantic
mapping are flagged, rather than inventing their data flow. Maintain
`MODULE_DETAILS` when introducing new semantic flows. The AST route inventory
does not claim to enumerate dynamically composed routes.

Each file has `analysis_status`: `measured` for parsed Python, `not_measured` for
other formats, `invalid` when parsing/decoding fails or `unavailable` when reading
fails. Functions are null unless measured; a real parsed zero remains zero.
The UI labels unmeasured functions/dependencies explicitly rather than implying
that JavaScript/HTML/CSS have none. Raw source and exception text are not exposed.
An unreadable file has no hash. Other files, routes and database diagnostics
remain available, with inventory `status=partial`, an architecture evidence
warning and `inventory_status` in the technical JSON report. Invalid/unreadable
files no longer count as verified mapping references. Partial inventories are
retried instead of cached; permission metadata changes invalidate complete cache
entries. A partial fingerprint is not a complete source revision. `complete`
only refers to the configured inventory scope, not every file in the repository
or a runtime correctness/security audit.

The technical inventory has a client-side search across filenames, listed local
dependencies and literal routes. Search ignores case/diacritics and requires all
space-separated terms to match each result; punctuation is literal, not a regular
expression. Counts show matching versus total items. The query survives refresh
and clinic changes, but is not stored or sent to the server/AI. Clear or Escape
restores the full inventory. This is not a search through source-code contents.
The file table is keyboard-scrollable on narrow screens; matching routes expand
while typing without being forcibly reopened by periodic refreshes.

The interface refreshes once per minute while visible. It does not probe external
APIs or trigger synchronization. A successful historical sync is not a live API
health check. A 24-hour threshold is an attention heuristic, not a failure SLA.
Date extents do not prove that every intermediate record was synchronized.
Meta's existing last-sync marker has no failure history; the UI says so.

Sync samples contain at most 10 attempts and report the actual sample size and
number of valid completed attempts. Pending or inconsistent rows are not counted
as failed attempts. An empty/unavailable history reports unknown measurements,
not zero failures. Numeric sync timestamps are validated, allowing up to five
minutes of clock skew; malformed metadata is never forwarded as raw text.

Diagnostic reads are isolated by source/table. An incompatible optional schema or
failed aggregate query marks the diagnostic `partial` without dropping unrelated
measurements. Counts remain available when only their date column is missing.
Missing counts are null, never coerced to zero. Dataset date ranges identify their
source column and distinguish missing, empty, invalid and non-applicable ranges.
Only the calendar validity of the two date bounds is checked; this is not a full
record-by-record date audit. The booking creation-date check measures presence,
not the correctness of every stored creation timestamp.

## AI

Read-only Chat Completions analysis on demand, no tools, no code execution and no
automatic repairs. Uses the clinic's existing OpenAI configuration, falling back
to Vielle when absent; the UI shows the configuration source. Optional
`SYSTEM_BRAIN_OPENAI_API_KEY` overrides the key for a dedicated installation.
`OPENAI_MODEL` retains the existing configured model (default gpt-4.1-mini).
The output uses a strict JSON Schema with evidence IDs constrained to the current
snapshot, and is validated again locally. Every finding must reference a supplied
evidence ID. Truncation and refusal are reported separately from invalid evidence.
Provider errors are replaced by safe messages. One analysis runs at
a time per process, with a 60-second per-Master interval and a 2000-token output
ceiling. Calls consume the configured API account; no background AI calls occur.

The consultative prompt has an explicit `assessment_scope` in its technical
input: local read-only snapshot, no live external API tests, no full period
coverage verification and no review of individual record contents. It separates
recorded outcomes from current availability, uses completed attempts as the
failure denominator, treats missing measurements as unknown rather than zero,
and does not equate local date bounds/counts with complete synchronization.
Unknown states do not justify diagnosing credentials, providers or network faults.
Empty findings are allowed instead of forcing generic suggestions.

New analyses record the server-owned `prompt_version` in their JSON payload and
technical export, without a schema migration or changes to historical records.
This identifies the instructions used; it is not a confidence or quality score.
Model, endpoint, output budget, timeout and key configuration are unchanged.
The prompt revision follows the official guidance on explicit instructions and
concrete interpretation examples: [GPT-4.1 prompting guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-4.1).

Offline tests verify the request contract, privacy, observation scope and
preservation of facts across ok/error/unknown/stale/running/partial fixtures.
They use mocked responses and **do not establish semantic quality of live model
responses**. Before publishing this prompt revision, manually review real or
synthetic provider outputs (only with a separately authorized API call) for:

- Successful local sync: reports a historical success, not current stability.
- Failed local sync: no invented cause such as an expired key or provider outage.
- Missing history: insufficient information, not a confirmed integration failure.
- Old/pending records: heuristic age or missing completion, not a verified outage
  or a currently running worker.
- Partial database: limits statements to available measurements; null is not zero.
- Date bounds and different counts: no claim of full coverage or financial
  reconciliation. Each finding cites a relevant supplied evidence ID.

Prompting and valid evidence IDs cannot guarantee the truth of generated prose;
the analysis remains advisory and must not trigger autonomous repairs.

Official API reference: https://developers.openai.com/api/reference/resources/chat
Structured output: https://developers.openai.com/api/docs/guides/structured-outputs

## Verification

`python3 -m unittest discover -s tests -p 'test_system_brain.py' -v`

`node --test tests/test_brain_data.cjs`

`python3 tests/serve_brain_preview.py` starts an ephemeral localhost-only preview.
Its printed URL signs into a synthetic Master. All data is temporary and removed
on exit; AI is mocked and external urllib requests are blocked. Do not use this
fixture server as a deployment entrypoint. Stop it with Ctrl+C after UI testing.
`--history-state partial` adds a synthetic malformed record; `--history-state
unavailable` simulates an incompatible history schema. Both affect only the
temporary fixture database and make no external calls.
`--inventory-state partial` adds one synthetic unreadable entry to test the
technical table and diagnostic warning without damaging any source file.
`--comparison-state legacy` omits inventory completeness from the older analysis;
`--comparison-state undated` omits its observation timestamp. These fixtures
verify unavailable comparisons without changing existing saved records.

Tests use temporary SQLite databases and mocked OpenAI responses. They exercise
real HTTP guards, CSRF, throttling, data preservation, metadata minimization,
cross-clinic module availability and inventory updates. No patient database or
live integration is modified by the test suite.
