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

## Contact Phone Editing and Removal

The lead profile and attendance record have a validated country/area-code phone
editor and a separate remove-phone command. Lead deletion is a distinct, labelled
trash action. Phone changes require confirmation, a contact revision and access
to every funnel sharing that contact. Another contact's number or a different
Experts patient is rejected rather than silently merging identities.

Changes keep contact/lead IDs, attribution, conversations, media and clinical
records. Old destinations remain in message/call history and a private phone edit
audit. New replies use the explicitly saved phone, not the old thread's peer;
stale composer destinations are rejected. Removal clears the active phone in
profiles and lists, blocks new WhatsApp/voice/automatic sends, and preserves the
identity phone privately for history/reconciliation. Later inbound events do not
reactivate it. A valid phone can be entered again from the same editor.

Queued messages and active bots are stopped on change, never silently redirected.
In-flight messages/calls must finish first; unconfirmed sends remain unconfirmed.
Automatic identity merges do not override a manually edited phone. CRM phone
editing does not modify any phone or financial record in Experts or Kiwify.

## Multi-Professional Attendance

Attendance has a checkbox-based professional selector, separate from the single
professional context used by funnels and reports. The selection is remembered in
the browser per signed-in user and intersected with the current permitted doctors.
Each conversation displays its WhatsApp connection name and, for a combined inbox,
its professional. Number/activity/search/unanswered filters cover that selection.

`inbox-multi` validates every selected owner and reuses the existing per-doctor
inbox, funnel and institute checks. Counters cover all matching conversations;
the combined display contains the first 200, with global ordering preserved.
No conversations or contacts are merged. Replies, attachments, search, closure,
profiles, stage changes, bots and transfer commands retain the chosen thread's
doctor context. Sender options never include another doctor's numbers. Switching
the selected professionals invalidates old responses and clears the open chat.
This is a read aggregation, not a permission grant or a connection transfer.

## Lead Transfers Between Professionals and Funnels

The lead profile and inbox commercial record have a Transfer lead command. The
Kanban and list selection toolbars also support transferring multiple selected
opportunities. The destination picker lists only permitted doctors, funnels and
their stages, including institute permissions. Both source and destination access
are checked again when the frozen, actor-bound confirmation is committed.

Transfer changes the existing opportunity's owner/funnel/stage in place. The lead
and contact IDs, notes, activities, value, follow-up date, attribution, opt-out and
source references remain intact; no contact or opportunity copy is created. A
contact already present in the destination (including its trash) blocks the whole
batch rather than silently merging or overwriting a different opportunity. Stale
lead or source/destination funnel revisions also block the whole transaction.

Old bot runs/tasks and pending events for the moved opportunities are cancelled
or consumed. Queued campaign recipients selected through those opportunities are
skipped; legacy campaigns without an audience snapshot skip the related source
contact. In-flight/unconfirmed automatic sends block transfer. Other opportunities,
bots and campaign recipients remain unchanged. Administrative transfer grants no
XP and emits no destination bot trigger, even when selecting a won stage.

WhatsApp connections, conversations and media remain with the original number and
doctor, under their existing access controls; transferring a lead does not grant
access to another doctor's inbox or alter a phone connection. Later messages to
the original number are retained there without automatically recreating the moved
opportunity in the former doctor's funnel. An explicit new opportunity there is
still possible. Clinical and course reports use the destination context without
writing to Experts/Kiwify or moving external financial records. `lead_transfers`
retains an original opportunity snapshot, route, actor, time and batch reference;
the standard authentication audit also records preview/apply. Deployment itself
does not move any live opportunities.

## WhatsApp Connection Transfers

In Numbers, the Master can transfer an existing channel to another professional
and select an open destination funnel/stage for future new leads. A confirmation
shows the channel history and pending-dispatch impact. The channel ID, provider
instance, pairing state, phone and sending limits stay unchanged; no provider
disconnect, reconnect or send is performed. Conversations and bound attachments
follow the channel, while opportunities, consent, opt-outs and clinical/course
records remain in place. Contacts are not copied.

The transfer is transactional and revision-checked. In-flight or unconfirmed sends
block it. Queued messages, including replies referencing the channel, are cancelled;
associated campaigns and explicitly bound active bots are paused, related bot runs
are cancelled, and pending channel bot events are consumed. Old-owner campaigns
cannot restart on the moved channel. Unsent personal uploads stay with their owner.
The previous/new routing, actor and impact are retained in `channel_transfers` and
the authentication audit. Existing doctor/funnel/institute permissions still apply.

## WhatsApp Connection Removal

Numbers has a Master-only, confirmed remove action. This is a reversible CRM
archive, not a provider logout, phone release or WhatsApp account deletion. Channel
IDs, pairing instances, contacts, opportunities, messages, media files and their
ownership remain intact. Keeping the provider instance also preserves the path
for retrieving historical attachments that have not been cached locally.

Removal rejects in-flight/unconfirmed sends, cancels related queued messages,
pauses associated campaigns and explicitly bound bots, cancels related bot runs
and consumes pending channel events. Archived channels ignore all new webhook
events, including connection updates, and cannot send, upload, reconnect, transfer
or be used to publish a bot. Lead and opportunity records are never deleted.

The normal inbox and operational waiting counts exclude removed channels. Their
history remains readable through the labelled number filter and contact record,
under the same permissions; existing attachments remain accessible. Removed
numbers are grouped in a collapsed section with Restore. Restoration reuses the
same ID in disconnected state without resuming campaigns, bots or cancelled sends.
Duplicate registration of an archived phone directs the Master to restore it.
Both actions are transactional, revision-checked and retained in `channel_lifecycle`
and the authentication audit. No production number is removed by deployment.

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
  exact-payload duplicate safeguards remain enforced. New manual replies are not
  blocked by an unrelated unconfirmed send, even in the same conversation. The
  duplicate check uses recipient, sender number, signed text and attachment hash;
  it also covers queued audio whose signature send has no confirmation. A queued
  duplicate is skipped if an earlier identical send becomes unconfirmed before
  dispatch. No throughput or anti-ban guarantee.
- A campaign is drafted first, deduplicated by contact, and explicitly started.
  Another campaign may intentionally contact the same person; this is not a
  cross-campaign marketing frequency policy.
- Each recipient needs documented opt-in for that doctor. Incoming messages do
  not grant marketing consent. SAIR/PARAR/STOP/CANCELAR suppress all queued
  messages to that contact clinic-wide, and staff can record other opt-out text.
- A disconnected channel pauses its campaigns; reconnection does not auto-start.
- Timeouts and crashes after a claim become `unknown`, pause the campaign and
  block automatic retries and automated dispatch for that number. Only newly
  requested manual messages carry `independent_manual_send=1`; the migration leaves
  old queued rows at zero, so deployment does not release a blocked backlog or
  resume campaigns/bots. Unconfirmed rows are never marked sent, cleared or retried
  just to unblock another reply. Inspect the phone before any manual reconciliation;
  cancelling a campaign does not clear an unknown send.
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

## Forms and automation workspace

The CRM rail includes Forms and Automations. Forms have editable drafts,
immutable published snapshots, signed anonymous submission sessions, private
answer files, CSV export and optional deduplicated opportunity creation. Clinic
and institute/course scopes remain separate. Uploads are bounded and unused
answer files expire after 24 hours; submitted attachments remain private.

Email notifications need CRM_SMTP_HOST, CRM_SMTP_PORT (587 or 465), CRM_SMTP_FROM,
CRM_SMTP_USER and CRM_SMTP_PASSWORD. Notifications contain a private CRM link,
not answers or attachments. Configure CRM_GOOGLE_CLIENT_ID and
CRM_GOOGLE_CLIENT_SECRET for the application's Google OAuth client, with the
callback https://doc4docs.com.br/api/crm/forms/google-callback?clinic=vielle.
DOC4DOCS_PUBLIC_URL overrides the public base URL. Each form connects through
the user's own Google consent flow with drive.file scope. Tokens are encrypted
on the persistent volume. No shared Codex Google connection is used. Google
row updates are deterministic and use RAW cells. Unconfirmed email deliveries
are not automatically retried. Missing configuration is shown in the editor.

Robots are created as inactive drafts. Only the Master can explicitly publish
or pause them. Saving an active robot edits only its draft. Publishing cancels
old executions and applies only to future live events, never historical imports
or all existing leads. A manual start is available from the opportunity record.
The supported blocks are message, form link, delay, condition, move stage, note,
task, tag, set opportunity field, close conversation and finish. No arbitrary
code, external webhooks, AI calls or Meta conversion events are executed.
Drawflow 0.0.60 is vendored with its MIT license, without a production CDN.

The bot worker stores each run, current block, due time and execution log in
SQLite. Local state changes and advancement commit in the same transaction.
WhatsApp sends use the existing durable queue. Unconfirmed sends stop the run
and are never automatically repeated. Bots and campaigns share each number's
200/day ceiling and 30-second minimum dispatch spacing; manual replies retain
their existing behavior. Descadastro is always respected. By default a new
customer reply or an unrelated stage change interrupts the run. Stage-trigger
chains are bounded, graphs reject cycles, and a contact has a bounded number
of daily launches. The activating user's access is rechecked by the worker.
The deployment does not create or activate customer robots.

The synthetic preview accepts --forms-fixture and --bots-fixture. It disables
all real sends and uses a loopback-only test session. Never expose that helper
as a production endpoint. Tests cover draft/public separation, private uploads,
deduplication, scoped access, opt-outs, races, restart recovery, queue limits,
explicit activation and preservation of the active version when editing.
