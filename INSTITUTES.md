# Institutes / Courses

Independent DOC4DOCS workspace at `/institutes.html` (alias `/institutos`).
The initial institute is `victor-paranhos`, with course `regen-code`.
This module keeps the existing Python HTTP server, vanilla frontend and SQLite.
Entering `/institutos` always opens account selection, including when only one
institute is available. Courses live inside the selected institute. The
catalog reads institute records from the central module database, supports
future entries and respects memberships. No placeholder institutes are created.

## Persistence And Access

- Database: `<auth database directory>/institutes/institutes.sqlite3`.
  The auth directory follows `DATA_DIR` / `RAILWAY_VOLUME_MOUNT_PATH`; no clinic
  database is migrated, reused or erased.
- New tables are created lazily, with WAL, private directory/file permissions
  and parameterized queries. Database files are ignored by Git.
- Existing login/session guards apply. Institute membership is independent
  of clinic membership. Master has access to all institutes and configures
  connections, course sources, campaign mapping and user access.
- Common members may read/export reports and start a read-only sync. They
  cannot access settings, credentials, product discovery or grant endpoints.
- Tokens never appear in API responses. Empty secret inputs preserve a
  stored token. Changing an account resets only that source's imported cache
  and product/campaign mappings, preventing cross-account totals.

## Connections

**Kiwify:** client ID, client secret, account ID, and selected course products.
Credentials stay server-side. Imports use paginated 30-day windows, with
history starting in January 2025 and boundary overlap for Brazil dates.
Only the selected products enter the course report. No refunds/orders are
created or changed in Kiwify.

**Google Sheets:** configured sheet ID and tab gid. Reads the form's exported
CSV and recognizes its existing name, email, phone, date, ID and UTM columns.
Private sheets can be imported as CSV by Master without making personal data
public. Responses are deduplicated by source ID and unique leads by contact.

**Kommo:** uses the existing Vielle connection only for the exact course funnel
name (initially `REGENCODE`). Every returned lead's pipeline is checked;
clinic funnels never enter the institute cache. No CRM lead is modified.

**Meta Ads:** independent institute ad account ID and an access token with
`ads_read` and account access. Master discovers and explicitly selects the
course campaigns, then maps their IDs / `utm_campaign` values. Insights
import daily spend, impressions and clicks only for the selected campaigns.
A campaign cannot be assigned to two courses in the same institute. Accounts
in other currencies are rejected rather than silently converted.

Optional environment overrides use this prefix:
`INSTITUTE_VICTOR_PARANHOS_` followed by `KIWIFY_CLIENT_ID`,
`KIWIFY_CLIENT_SECRET`, `KIWIFY_ACCOUNT_ID`, `META_ACCOUNT_ID`,
`META_ACCESS_TOKEN` or `META_VERSION`. Never commit actual values.

## Report Rules

- Revenue includes only approved/paid orders by approval date in Brazil.
  Pending, refused, refunded and chargeback orders are not revenue.
- Gross/net monetary values are integer cents. Net is the Kiwify-reported
  amount, not the withdrawable balance or final profit.
- Invalid dates/amounts/currencies are excluded and flagged for validation.
- Conversion tracks unique form leads captured in the period who purchased
  in that same period. Contact matches use email/phone, never name alone.
- Sale UTMs take precedence over form UTMs. Ambiguous matches are flagged,
  not guessed. Attribution from form dates is day-level, not intraday.
- CPL/CAC/ROAS require explicit campaign mapping and complete synced spend
  coverage. Organic/unmatched sales cannot inflate paid-campaign ROAS.
- Filters accept daily date ranges up to 366 days. XLSX exports include
  summary, campaigns, daily metrics and all filtered orders, not just a page.
  Customer strings are exported as text to prevent spreadsheet formulas.
- A failed source preserves its last data and exposes a sanitized error.
  Successful imports replace only their own source snapshot/date window.
  Refreshes are per course with an in-progress lock and 30-second cooldown.
  This initial module uses manual refresh; clinic periodic jobs are unchanged.

## Files

- `institute_store.py`: independent storage/configuration/membership.
- `institute_sources.py`: read-only source connectors and normalization.
- `institute_report.py`: reconciliation, metrics and XLSX.
- `institute_api.py`: authorization, HTTP routes and background refresh.
- `static/institutes.html`, `.css`, `.js`: institute workspace.
- `static/institute-entry.js`: clinic-home link and institute-only landing.
- `static/institute-chart.min.js`: vendored Chart.js 4.5.1, MIT license.
- `app.py`, `auth_http.py`, `static/login.js`, `static/session.js`,
  `static/index.html`, `static/master.html`: scoped routing/access links.

## Local Verification

Run from the repository with its existing Python dependencies:

```sh
python3 -m unittest discover -s tests -p 'test_institutes*.py' -v
node --test tests/test_institutes.cjs
python3 tests/serve_institutes_preview.py
```

The preview helper uses a temporary database and synthetic data only. It
prints its loopback URL and provides `/__preview__` for a demonstration login.
Never deploy this helper or use its demonstration login on a public server.

UI verification uses Playwright and the locally installed Chrome:

```sh
node tests/check_institutes_ui.cjs http://127.0.0.1:PORT/__preview__
```

Set `PLAYWRIGHT_MODULE` when Playwright is not on Node's module search path;
`PLAYWRIGHT_CHANNEL` defaults to `chrome`. Screenshots are saved outside the
repository in `../institute-preview`. The check verifies desktop/mobile,
nonblank chart pixels, filters, tables, integration forms and dialogs.

Configure production credentials through
Master or Railway variables after an explicitly authorized publication;
local credentials/databases are never included in the code deployment.
