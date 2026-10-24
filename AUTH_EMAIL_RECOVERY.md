# Email sign-in and password recovery

Prepared locally on 24 October 2026. Publication and live SMTP authentication
require a separate confirmation. No real user identities or passwords were
changed while preparing this feature.

## Identity model

- Existing users, IDs, passwords, memberships, CRM scopes, team profiles and
  history remain in the same central authentication store.
- Schema five adds a nullable unique email plus recovery and rate-limit tables.
  It does not infer email addresses, clone users, change access or revoke valid
  sessions merely because the schema was initialized.
- New users in Master must have a normalized email as their login. The Master
  must explicitly confirm ownership of the address. Existing users can retain
  their legacy login while the Master associates their actual email.
- Addresses must be verified administratively with the intended person before
  saving. There is no automatic email-ownership verification flow in this release;
  an administrative confirmation is not a claim of mailbox verification.
- Email/login aliases cannot collide across users. Email changes require an
  explicit confirmation, revoke outstanding recovery links and all user sessions,
  and produce a sanitized audit event. No existing account is mapped automatically.

## Recovery boundary

Public pages: `/recuperar-senha` and `/redefinir-senha`.
POST endpoints: `/api/auth/password/forgot` and `/api/auth/password/reset`.

The server creates a cryptographic random bearer with twenty-minute expiry and
stores only its digest, user, current email and password-version fingerprint.
New requests replace prior links. Consumption is transactional, single-use and
rechecks active status, email, password version and expiry. Success revokes all
sessions and all outstanding links; it does not automatically sign the user in.
Administrative password/login/email changes, deactivation and Master promotion
also invalidate outstanding links.

The actual bearer is sent only in the email link fragment. It is not embedded
in HTML/JavaScript, API response payloads, localStorage, sessionStorage or audit
logs. The reset script removes the fragment from the visible URL immediately
and keeps it in a closure until submitting the new password. Knowing the public
page or source does not supply a valid bearer. Anyone who obtains the email link
can use it until expiry or revocation; email account security remains essential.

Requests use the same generic result for known, unknown, inactive and throttled
addresses. A bounded asynchronous queue separates SMTP timing from the public
response. Persistent counters bound requests by normalized email and socket
peer, token consumption by peer and SMTP delivery globally. A generic queue
result does not guarantee delivery. Restart can discard queued requests; users
can request again after the cooldown. There is no automatic SMTP retry after an
uncertain result and no plaintext token stored in a durable outbox.

Same-origin/custom-header protection, bounded JSON bodies, no-store caching,
no-referrer, framing denial, restrictive recovery-page CSP and redacted access
logging apply. SMTP/provider exception bodies never reach responses or logs.

## Server settings

- `DOC4DOCS_AUTH_EMAIL_ENABLED`: only `1` enables SMTP recovery. Default disabled.
- `DOC4DOCS_SMTP_HOST`: the configured provider hostname; Hostinger uses
  `smtp.hostinger.com`.
- `DOC4DOCS_SMTP_USER`: the full sender mailbox address, configured by the owner.
- `DOC4DOCS_SMTP_PASSWORD`: the mailbox password, entered directly by the owner
  into the provider's protected server variable control. Never commit or paste
  it into chat, screenshots, documentation or project memory.
- `DOC4DOCS_AUTH_PUBLIC_URL`: explicit canonical HTTPS origin. It never comes
  from Host, Forwarded or other incoming request headers.

This adapter uses verified implicit TLS on port 465 and TLS version 1.2 or newer.
There is no insecure/plaintext fallback, certificate bypass, IMAP or inbox read.
Envelope sender and From use the authenticated mailbox. The same worker can
send a password-changed notice, without a password or bearer in that notice.

Railway currently documents SMTP as Pro-and-above; upgrading requires redeploy
before SMTP availability changes. Do not change plans to troubleshoot without
approval. Verify actual plan, DNS authentication, connectivity and delivery.

## Deployment and rollback

Before release, verify the authorized revision, native/external backup status,
and a rollback plan. Deploying changes authentication code and performs an
additive migration of the central auth database. Existing unmodified logins
continue to work; email recovery stays disabled until settings are complete.
Do not alter real passwords or trigger customer recovery emails to test it.

After explicit authorization, connect the selected mailbox using only protected
server configuration and send one low-impact test to the owner's approved test
inbox. Verify arrival and sender; SMTP acceptance is not inbox delivery. Do not
read unrelated inbox messages or change DNS records merely because a setup page
suggests doing so.

After that authorization, `python auth_mail.py test-sender --confirm` sends one
static configuration-test email only to the configured sender itself. It does
not create a user, generate a recovery link, read any database or start the app.
Its success means SMTP acceptance, not verified inbox arrival. Errors are fixed
codes and never print provider response bodies or settings.

Rollback is not a blind redeploy of schema-four code: that version deliberately
rejects schema five. Stop the affected service only after approval; use a reviewed
schema-five-compatible rollback or a separately authorized restoration with
reconciliation of changes since the backup. Never overwrite the auth volume or
delete users to recover compatibility.

## Verification scope

Synthetic tests exercise migration, legacy/email login, collision handling,
expiry, single use, concurrent consumption, session revocation, identity changes,
Master enforcement, generic responses, request limits, TLS and safe provider
errors. Tests run without external network, real mailboxes or production data.
The existing large application-wide HTTP suite must be distinguished from the
focused real authentication/mixin HTTP suite. Record which suites were executed.

## Official references

- https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html
- https://www.hostinger.com/support/1575756-how-to-get-email-account-configuration-details-for-hostinger-email/
- https://www.hostinger.com/support/4625828-parameters-and-limits-of-hostinger-email/
- https://docs.railway.com/networking/outbound-networking
