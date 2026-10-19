# CRM Vielle Pilot

Permission-scoped Vielle pilot. Phone pairing requires a separately configured
Evolution v2 service. Outbound sending is disabled by default.

## Data and scope

- Vielle only. Access follows the existing `crm.view` permission and clinic
  membership assigned by the Master; no permission is granted by a migration.
  This module permission enables routine CRM work, chats, calls and campaigns.
  Number pairing, doctor/pipeline setup, tier rules, Kommo migration and global
  campaign activation remain Master-only, enforced on the server and hidden in UI.
- Separate database: `<auth data root>/crm/vielle/crm.sqlite3`.
- A doctor owns multiple pipelines and channels. Every lead, stage and campaign
  is validated against that owner. Contacts share identity and a clinic-wide
  suppression flag; histories and campaign consent remain scoped to the doctor.
- Bootstrap explicitly copies only the configured doctor/pipeline names from the
  existing Vielle mapping. It does not import Kommo stages, contacts or chats.
  New stages can be reordered/renamed. Occupied stages cannot be deleted or have
  their open/won/lost meaning changed.
- No writes to Kommo, Clinic Experts, sales or financial records. CRM values and
  won status are manually entered opportunities, not reconciled sales/payments.
- Every inbound message and opportunity save resolves the phone against the
  Vielle `clinica_patients` cache, opened read-only. The small phone index refreshes
  at most every 15 seconds; it is not a real-time Clinic Experts API lookup.
  Existing integration synchronization remains the source of freshness.
- A unique full phone (country + DDD + subscriber number) links the Experts UUID
  and uses that registered name. Never match using only the final 8/9 digits or a
  similar name. Multiple matches require review; a missing/empty/unavailable cache
  is `unchecked`, not evidence of a new patient. A non-match is explicitly only a
  non-match in the synchronized base. No patient is created in Experts.
- A later Experts registration is reconciled on save, inbound event or CRM reload.
  Multiple registered phones for one Experts UUID reuse an existing contact and
  preserve aliases. Same-phone duplicate inbound events reuse the opportunity.
  Pre-existing conflicting CRM contacts are flagged for review, not blindly merged.
- The pilot listing is limited to 1,000 opportunities per doctor and 100 recent
  campaigns. Scale to paginated queries before expanding the pilot.

## Patient commercial history

The client record includes closed sales and pending-quote tabs and shortcuts from
the chat's commercial panel. The read-only reader uses the Vielle `clinica_sales`
cache, with exact Experts patient UUID linkage; shared/unconfirmed identity never
exposes history. Missing cache is shown as unavailable, not a zero purchase total.

Default scope is the selected doctor, using the configured Experts UUID or explicit
full name when no UUID mapping is available. Conflicting UUIDs and unknown ownership
are not guessed. Authorized CRM operators can explicitly select the whole clinic.
This does not open access to another clinic or users without CRM permission.

Sales are sold values, not receipts. Cancelled/deleted/refunded/inactive sales are
excluded. Quotes display open, expired and lost statuses (consistent with the current
pending-budget policy); won quotes and quotes explicitly linked to a valid converted
sale are excluded. Another purchase alone never proves conversion. CRM opportunities
and XP do not manufacture Experts sales. Historical dates are not limited to the
current year; availability depends on the existing synchronized cache.

Amounts retain cents; missing/invalid amounts are explicitly partial, never inferred
as zero. Dates, professional, source UUID and procedures/products are shown. Results
are paginated at 25 records while totals reflect the entire filtered cached history.
No source JSON, credentials, new external requests or writes to Experts are involved.

## Patient Appointments

Opportunity cards and the chat's client panel show the next valid Experts booking
for the selected doctor, or the latest past booking if no future/in-progress one
exists. Commercial follow-up dates remain separately labelled. A past time does
not prove attendance: the actual source status remains visible. Cancelled bookings
never drive card suggestions; deleted bookings are omitted entirely.

The client record has an Appointments tab with dates, times, procedures and status,
upcoming/past filters, pagination and an explicit whole-clinic scope for the Master.
Cancelled entries remain identifiable in history. The default doctor filter uses
configured UUIDs or explicit full names, never names/phones to match patients.

The reader opens the Vielle cache read-only and batches patient UUIDs, so a board
does not perform one external request or database connection per card. Empty or
unavailable synchronization is not presented as proof of no booking. Inconsistent
identities/dates are flagged, not guessed. Missing times stay unconfirmed instead
of being invented as midnight. Naive timestamps use Sao Paulo; offset timestamps
are converted to that timezone. An active booking requires an explicit valid end
time to be labelled in-progress. The visible board/chat refreshes from the local
cache every minute, without interrupting a drag, resetting the board's horizontal
position, or clearing chat drafts. This does not itself call the Experts API.

## Gamification

Daily missions and cumulative XP are per doctor, Sao Paulo time. One follow-up
per opportunity/day earns 3 XP; the first advance to a new open stage earns 5 XP;
the first win per opportunity earns 25 XP. Moving backwards and forwards cannot
repeat awards. Imports, notes and broadcasts earn no points. Actor display names
are sufficient for the Master-only pilot; use stable user IDs for team rankings
before opening access to employees.

## WhatsApp adapter

### Customer Classification

- Bronze below BRL 5,000; Silver from BRL 5,000; Gold from BRL 15,000.
  A Master can adjust both exact-cent thresholds for the Vielle pilot. The rules
  are shared, but purchase totals stay scoped to the selected doctor.
- Classification uses all years of valid closed Experts sales, joined by exact
  patient UUID. Opportunity estimates, quotes, cancelled/deleted/refunded sales,
  other patients and other doctors never contribute. This is purchase value,
  not a claim that payments were received.
- Source summaries are batched in chunks of 400 under one read-only SQLite
  snapshot. Listing does not make per-card API calls or rewrite source records.
- Missing identities, unavailable history and partial sales history receive no
  invented category. Quote-only warnings do not contaminate valid sales totals.
- Funnel cards use bronze/silver/gold backgrounds and borders, with 16px labels
  in the existing header alongside the name, not a separate full-width badge row.
  Their accessible names and hover titles retain the exact category and total.
  Compact metallic badges remain in chat headers; the contact
  panel and editable record show purchase totals, count and next threshold.
  The board has category filtering and a deduplicated current-funnel breakdown.
  Existing shared unread/close semantics are unchanged.

### Search And Segmentation

- The shared search bar on funnels and inbox supports names, phones, opportunity
  titles, exact-patient Experts CPF/email, and own-doctor sale/quote descriptions
  and procedures. Accents, case and formatted numeric searches are normalized.
  Internal search strings are removed before returning results to the browser.
- Combinable filters include identity/link status, tier, has/no closed sales,
  inclusive minimum/maximum cumulative purchase value, minimum sale count, and
  open/pending/lost/expired/no pending quotes. Open means active only; pending
  includes active, lost and expired. Converted/won/deleted quotes are excluded.
- Financial filters require complete, safely linked source histories; unknown or
  partial histories never masquerade as zero purchases. Sales and quote coverage
  are evaluated separately. The UI discloses incomplete histories excluded by
  current financial filters. Source data stays read-only and doctor-scoped.
- Filters run on the server before the 1,000-lead/200-thread display caps. Ordering
  supports recent activity, largest purchases and most closed sales. Unknown
  purchase histories sort after known values. Open chats and unread markers are
  preserved when a filter removes the conversation from the result list.

### Ponto de atendimento

- Clicking an opportunity opens a chat-first workspace. Desktop has conversations,
  chat and commercial details; mobile alternates the list/chat and opens details
  in a side panel. The original editable record and activity history remain available.
- Filters: doctor, sender number, all/unread/waiting, and name/phone/message search.
  At most 200 threads are listed; refine search beyond that. Messages load in pages
  of 80 with an explicit older-history control.
- Unread/pending state is shared across the team, scoped to each channel/contact.
  Opening, scrolling and polling never clear it. Only a manual reply accepted by
  the connector or an explicit `Fechar conversa` resolves the rendered inbound
  boundary. Queued, sending, unknown and campaign messages do not resolve it.
  A reply from another number resolves only its source thread. New arrivals after
  the captured boundary remain pending; duplicate webhooks do not reopen it.
  Legacy per-user view markers are retained but ignored under this policy.
  These are DOC4DOCS markers, not WhatsApp read receipts. Closed threads remain
  in All and reopen as pending on the next inbound message.
- Draft text is retained per conversation in page memory, not persisted remotely.
  Replies use idempotency keys and the last inbound telephone rather than silently
  sending to a different primary phone on a patient with multiple aliases.
- The reply composer selects the active conversation's sender number by default.
  Opening from a funnel uses the contact's most recent inbound channel, independent
  of inbox filters. Staff can explicitly select a different number owned by the
  same doctor; disconnected numbers cannot send. Cross-doctor handoffs are rejected.
  A handoff retains the source conversation's recipient telephone, then opens the
  newly selected number's thread after queuing. Polls never reset a chosen sender.
- New replies and campaign messages carry `*Authenticated user name*` followed by
  a blank line and the message. Server-side name snapshots and stable user IDs are
  retained per message; client-supplied sender names are ignored. Names are kept to
  a single line without formatting delimiters. The chat renders the title in bold.
  A renamed user does not alter previous messages or duplicate an idempotent retry.
  Existing unsigned messages are not retroactively attributed to someone else.
- Manual text replies require an enabled worker, connected number and a recent
  inbound text (24 hours) or documented contact authorization. Marketing opt-in is
  still required for campaigns. This local guard is not an official-API policy check.
- This first adapter ingests new inbound text and DOC4DOCS-sent messages only;
  texts sent externally on the handset, historical chats, files, audio and read/
  delivery receipts are not synchronized yet. Pairing remains unvalidated.
- Stage animations occur only after a successful save, use real awarded XP and
  distinguish open, won and lost phases. Reduced-motion preference suppresses
  animated effects. Moving again never duplicates XP.
- Phase changes include a 1.5-second, non-blocking full-screen transition showing
  the actual origin/destination; wins use a 2.1-second celebration. Reduced-motion
  users receive only the concise static confirmation, never the screen animation.

Evolution API **v2** is accessed only by the backend. This connector has not been
validated against a running external instance yet. It is not compatible with an
arbitrary provider or v3 by changing the URL alone.

Required server environment:

```
CRM_EVOLUTION_URL=https://your-controlled-evolution-v2-host
CRM_EVOLUTION_KEY=<server-side api key>
CRM_WEBHOOK_URL=https://doc4docs.com.br/api/crm-webhook
CRM_WEBHOOK_SECRET=<random secret of at least 32 characters>
CRM_SEND_ENABLED=0
CRM_CAMPAIGNS_ENABLED=0
```

Use TLS or a private encrypted network. The gateway URL is administrator-managed,
not user supplied. Redirects are disabled to prevent API-key leakage. Custom
webhook header: `X-DOC4DOCS-Webhook`. Supported events: `messages.upsert` and
`connection.update`. Text-only in this first pilot; groups, LID-only peers,
history backfills, media and delivery/read receipts are not imported yet.

After authorized provisioning and pairing, test one opted-in test number before
enabling campaigns. Never pair or send automatically during a preview. No remote
session deletion, logout, role changes or credential exposure is implemented.

## Queue

The production entrypoint starts a background worker in the web process only
when `CRM_SEND_ENABLED=1`. It uses the same persistent volume, matching the
existing single-replica SQLite deployment. Do not provision an extra Railway
service expecting to share a mounted volume. A standalone worker can also be run
with `python crm_worker.py` on the same host/DATA_DIR, for controlled tests.
Set `CRM_SEND_ENABLED=1` only for an explicitly authorized live test. Campaigns
also require `CRM_CAMPAIGNS_ENABLED=1`; without it, the API blocks campaign starts
and the worker skips campaign queue entries even if previously marked running.
Manual replies retain connection, consent/recent-inbound and idempotency checks.
The UI
requires a recent worker heartbeat, a configured gateway and an open channel.

- SQLite immediate transactions serialize claims across workers.
- Campaigns only: configurable interval (30-3,600 seconds), daily cap (1-100),
  scheduled start and sending hours (08:00-20:00 Sao Paulo).
- Manual replies have no DOC4DOCS daily quota and do not wait for campaign
  intervals or consume campaign quota. They retain queue priority. One in-flight
  send per channel, connection, consent/recent-inbound, opt-out, idempotency and
  unknown-send safeguards remain unchanged. No throughput or anti-ban guarantee.
- A campaign is drafted first, deduplicated by contact, and explicitly started.
  Another campaign may intentionally contact the same person; this is not a
  cross-campaign marketing frequency policy.
- Each recipient needs documented opt-in for that doctor. Incoming messages do
  not grant marketing consent. SAIR/PARAR/STOP/CANCELAR suppress all queued
  messages to that contact clinic-wide, and staff can record other opt-out text.
- A disconnected channel pauses its campaigns; reconnection does not auto-start.
- Timeouts and crashes after a claim become `unknown`, pause the campaign and
  block automatic retries and further dispatch for that number. The pilot cannot
  reconcile ambiguous sends yet. Inspect the phone before any manual reconciliation;
  cancelling a campaign does not clear an unknown send. Do not clear unknown states
  to blindly retry. This intentionally leaves the channel blocked pending review.
- Sent means accepted by the connector, not delivered or read. Pause/cancel may
  not interrupt a message already in flight. Provider acceptance plus DB failure
  is inherently ambiguous; no claim of exactly-once external delivery is made.

Intervals and daily limits are workload controls, **not anti-ban guarantees**.
No account rotation, identity spoofing, anti-spam bypass or unsolicited lists.
Non-official integrations can violate WhatsApp terms and lose access. Use the
official API for recurring or high-volume campaigns.

## Preview and verification

The shared floating attendance launcher is visible only to an authorized Vielle
Master in clinic screens and administration, never in public screens or another
clinic/institute context. It opens `crm.html?clinic=vielle&mode=inbox`; within the
CRM it switches tabs without navigating away or discarding the chat draft.

Appointment segmentation is shared by the board and inbox. Date bounds are
inclusive Sao Paulo calendar days and use the entire synchronized history for
the selected doctor, not just the next/last summary. Completed appointments
require an explicit attended/completed source status; past unconfirmed visits
and no-shows do not qualify. Cancelled/deleted records never qualify.
The previous-month return shortcut combines completed visits with no upcoming
appointment for that doctor, including appointments outside the chosen range.
Unavailable, ambiguous and partial histories are excluded with a coverage count,
not treated as an empty agenda. One batched read supplies segments and card
summaries before display limits. Filtering never changes source records or
conversation resolution/unread state.

`python tests/serve_crm_preview.py` creates isolated synthetic data on loopback,
disables sends and removes gateway credentials from that process. Never deploy
this preview helper or synthetic authentication route. Store/API tests use temp
directories and mocks and never send external messages.

Verification: store/queue/patient-identity tests, authenticated HTTP routes,
permission regression and shared-header notification tests. The loopback UI
exercise covers doctor switching, new funnels, lead creation/movement and stage
feedback, inbox unread/number filters, preserved drafts, chat and commercial panel,
draft campaigns, disabled live sending, mobile menu and bounds at 320/390/768/1440px.
Actual external pairing, sending and webhook delivery still require a separately
configured provider and an opted-in test phone. This is not evidence of a live
WhatsApp connection or live Clinic Experts synchronization.

## Patient creation and explicit refresh

New opportunities can start with a manual CRM contact or a selected patient from
the Vielle Experts directory. The picker searches names without accents and
full/partial phone numbers, pages 25 results, and exposes only UUID, name and
selectable phone numbers. Deleted patients are omitted. Missing/shared phones
cannot be selected; multiple unique registered phones can be chosen explicitly.
The server revalidates the selected UUID against the full phone and uses the
source name. Existing contacts and opportunities in the same funnel are reused.
Selection does not grant messaging consent or create a patient in Experts.

The ficha and attendance panel have an explicit refresh button; the picker also
has a directory refresh. They share one deduplicated background job, serialize
with clinic synchronization and fetch the patient directory via the existing
server-side Experts integration. The complete response is staged before a local
transaction updates the cache. Failures preserve the previous cache, report a
sanitized error, and do not expose provider responses. No external records are
written, and no financial synchronization or message dispatch is started.
Patient reconciliation preserves conversations, drafts, unread state and history.
A changed or shared patient identity is flagged for review, not silently merged.

`tests/serve_crm_preview.py --patient-refresh-fixture` additionally replaces the
patient refresh function with an isolated synthetic fixture. Never use that
flag/helper in production. UI verification covers manual creation, existing
patient reuse, draft/unread preservation after refresh and 390px modal bounds.

## Pending quotes on cards

Cards, funnel totals, stage totals and the attendance panel show the sum of
active and expired Experts quotes for the matched patient and selected doctor.
Lost, cancelled, deleted and converted quotes and closed sales do not enter.
The amount is computed from the complete synchronized history, not a page of
results, and does not overwrite the CRM's manually entered estimate. Cards with
no pending quote omit the currency amount. Unavailable/ambiguous/partial quote
histories show a review state rather than an authoritative zero. Totals exclude
these unknown amounts and disclose partial coverage. Bronze/silver/gold remains
based only on valid closed sales. No provider writes or new per-card API calls.
