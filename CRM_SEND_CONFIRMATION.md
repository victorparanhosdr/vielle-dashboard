# Send confirmation repair: local checkpoint

Status: implemented, tested locally and authorized for publication. Deployment
success and the active revision must be verified separately; a push is not proof.
Base inspected: 727970d43638c0f8aa736d1112ce905f321bfdaf.

## Source findings

- A matching outbound webhook previously stored its provider identifier without
  settling the pending message. A subsequent HTTP timeout could mark that same
  message unknown and pause the campaign despite the recorded confirmation.
- Worker handling combined the provider request and local finalization in one
  exception handler. A finalization error was treated as another send failure.
- The adapter discarded error categories. The historical generic error does not
  prove which provider/network error happened in any particular live attempt.

## Prepared changes

- Correlate a unique outbound text echo using channel, recipient, exact body and
  a bounded provider timestamp. Historical imports and ambiguous matches do not
  settle pending messages. The existing authenticated webhook boundary remains.
- Preserve a confirmed acceptance across a later HTTP error; settle late
  confirmations without resuming a paused campaign or enrolling new follow-ups.
- Keep genuinely uncertain sends unknown and never retry them automatically.
  `sent` still means connector acceptance, not confirmed delivery or reading.
- Separate local finalization errors from provider request errors.
- Reject reuse of a provider identifier belonging to a different native message
  as uncertain without crashing the worker or cross-linking recipients.
- Keep only static error categories and user-facing labels. Provider response
  bodies, credentials, message content and recipient data are not logged.
- No schema migration, retrospective scan, production data edit, quota increase,
  campaign start/resume or connector reconfiguration.

## Recipient-scoped continuation (latest local increment)

- On explicitly started campaigns using `recipient_uncertainty_guard`, one
  timeout, HTTP 400 refusal, malformed response or missing confirmation ID is
  retained as an unknown message with a static reason. Other recipients continue
  with the existing schedule, channel gap, quota, opt-out and scope checks.
- Uncertain contacts/phone numbers remain excluded from further campaign
  attempts on that channel, including another campaign. No automatic resend,
  deletion, fake acceptance or automatic human-review acknowledgement is made.
- Three consecutive uncertain attempts within fifteen minutes pause the campaign
  for connector review. A successful attempt breaks the streak; an explicit
  exclusion during human-reviewed resumption also breaks it.
- Authentication, provider limits, network failures, HTTP 404/5xx/other unmapped
  HTTP errors, reused confirmation IDs, unexpected errors and stale worker claims
  still pause immediately. HTTP 400 is not asserted to prove an invalid phone;
  it may be a payload issue, and repeated failures reach the same circuit breaker.
- A guarded campaign with no queued/in-flight recipients becomes done while
  retaining any unknown records. The UI says "Concluida com pendencias", not that
  every message was accepted. Accepted and uncertain totals remain separate.
- Legacy running queues without the recipient guard retain conservative
  behavior until explicitly reviewed/started. Paused/cancelled campaigns are
  never resumed by this change, a late confirmation or a stale-claim recovery.
- Existing follow-up and bot uncertainty behavior is unchanged.

## Validation state

- Python AST parsing of the three modified modules and the new test module: OK.
- `git diff --check`: OK at preparation checkpoint.
- Thirteen synthetic regression cases in `tests/test_crm_send_confirmation.py`:
  echo before timeout, late echo, known legacy echo identifier, idempotent hooks,
  mismatched/missing timestamps, ambiguous matches, history isolation, diagnostic
  privacy, no resend, reused provider identifiers and finalization separation.
- Local execution on the user's Mac, authorized on 23 October 2026: 218 tests
  passed, zero failures/errors/skips, in eleven scoped unit-test modules.
- Scope: core CRM, confirmation repair, campaigns, media, manual uncertainty,
  worker recovery, external messages, follow-up runtime/settings/contact/actions.
- Latest recipient-continuation snapshot: 231 Python tests passed in those
  eleven modules, zero failures/errors/skips; eleven Node campaign UI tests also
  passed. This supersedes the earlier 218-test count for the current sources.
- Thirteen additional backend tests cover per-recipient continuation, interval
  and quota preservation, disconnected channels, systemic errors, circuit
  threshold/window/reset, legacy behavior, cancelled/paused state preservation,
  cross-campaign recipient exclusion and late settlement without follow-up.
- Three additional UI tests cover continuation notices, finished-with-pending
  labeling with truthful counts, and the ordinary fully accepted finished label.
- Validated source bundle SHA-256:
  `9f326812903a2f5cdf29a5c1641c61e76bb665af1fc6763652b421175205bb5f`.
- One new ambiguity fixture was corrected to represent a campaign message plus
  a manual pending message, rather than violate the campaign/contact uniqueness
  constraint. Two audio fixtures now return distinct identifiers for signature
  and audio, and assert the audio message actually becomes accepted.
- The earlier static-only checkpoint is superseded for these ordinary regression
  tests, not for the separate security audit or restoration validation.

Execution used an inspected source-only snapshot, existing phone-number package,
Python 3.12.14, synthetic temporary SQLite databases and simulated providers.
macOS Seatbelt denied network, source writes, and file reads outside the declared
OS/runtime/source/dependency/scratch paths. Trusted probes confirmed network and
out-of-scope read denial, read-only source, scratch writes and no production
environment variables before loading target code. CPU, process, file-size,
open-file and wall limits were applied; temporary sources and fixtures were
deleted after each execution. No real credentials, app.py, production databases,
uploads, connectors or live recipients were included.

This is NOT a complete security-audit sandbox verification. In particular no
OS-enforced memory or cumulative scratch-disk limit was established on macOS.
Do not present the regression result as completion of the Cloudflare audit.
No HTTP listener, real-browser layout or live provider compatibility test was run.
UI tests use the existing synthetic DOM/Node harness. They execute directly in a
single process under the same file/network restrictions after isolation probes;
the target cannot spawn a second test process under the current process limit.

Run the focused tests plus existing CRM campaign, manual uncertainty, media,
worker recovery and follow-up suites only in an isolated temporary environment
with synthetic databases, no production environment/secrets or external network.
Do not use live recipients as a test fixture.

## Next boundary

The user authorized publication after the ordinary local regressions above.
Publish only this repair, then verify the active deployment and served asset.
Keep the live campaign paused. Its historical unknown recipient has not been
reclassified, retried or removed. A future webhook is not guaranteed to arrive.

Unrelated offsite-backup files in this checkout must remain outside this repair's
eventual commit/release. Security/backup provisioning remains separately paused
until requested by the user.
