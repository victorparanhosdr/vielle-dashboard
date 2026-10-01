# Automatic clinic synchronization

The Python server runs the scheduler in `periodic_sync.py`. It does not require
an open browser, a Codex automation, or an external cron service.

## Schedule

- Every clinic in `clinic_catalog.py` participates; disconnected integrations
  are skipped. The default interval is five minutes after a cycle completes.
- Recent cycles use incremental Kommo leads/events with a two-minute overlap.
  The cursor advances only after all incremental streams complete successfully.
  An empty Kommo database requires an initial full import.
- Recent Clinica Experts and Meta Ads queries cover today and the previous six
  days, using existing integration date filters. Recent Experts cycles do not
  reload the complete patient/procedure catalog or expand financial dates by
  75 days. Embedded new patients are inserted without replacing full profiles.
- At 03:00 America/Sao_Paulo, each clinic receives a full Kommo reconciliation,
  the existing Experts historical import from its configured history start,
  including patient/procedure catalogs, and current-month Meta Ads data.
  Changes to older Experts records outside the recent window wait for this
  reconciliation or a manual update.
- Initial startup waits one interval before the first recent cycle and schedules
  the first reconciliation for the next 03:00. Persisted missed daily runs are
  caught up after a restart. Failed cycles wait for their next scheduled run.
- Clinics run serially. API delays, large imports, and manual work can extend
  the effective interval. Five minutes is not an API-completion guarantee.

## Configuration and safety

Master settings expose `AUTO_SYNC_ENABLED` and `AUTO_SYNC_INTERVAL_MINUTES`
per clinic. The interval is clamped to 5..1440 minutes. Changes apply during
the next scheduler check. An environment variable `AUTO_SYNC_ENABLED=false`
disables the server worker globally. Environment defaults are five minutes
and `AUTO_SYNC_HISTORY_HOUR=3`; the former `SYNC_INTERVAL_MINUTES` no longer
controls this worker.

Manual and automatic jobs share `ClinicRefreshJobs` and clinic integration
mutexes. Existing manual buttons remain available under the same authorization
rules. The dashboard polls job status every 30 seconds when idle, but never
initiates an automatic import from the browser. It delays reloading while a
visible form has edits or a dialog is open. Task-board refreshes do not replace
the task editor.

Locks are process-local. This configuration requires one application process /
Railway replica for the shared SQLite volume. Multiple replicas would require
a cross-process lease or a dedicated worker before enabling this scheduler.

No schema migration or reset is performed. Scheduler metadata and the Kommo
cursor use existing `app_settings` rows in each clinic database under `DATA_DIR`.
Authentication, stored follow-ups, exams, attachments, and manual histories are
not removed. Existing integration logs retain diagnostics; public job status
does not expose provider payloads or credentials.

## Local verification

```sh
python -m unittest tests.test_periodic_sync tests.test_clinic_refresh tests.test_quote_followup tests.test_financial_competence tests.test_financial_receipts tests.test_auth_http
node --test tests/test_clinic_refresh.cjs
python tests/serve_periodic_preview.py
```

The preview prints a localhost URL and uses temporary databases, a synthetic
Master, simulated integrations, and blocked external requests. Its first
simulated cycle starts after approximately 15 seconds; subsequent cycles use the normal
five-minute interval. Production API imports are not tested by this preview.

## Files in this change

- `periodic_sync.py`: scheduler and clinic integration mutexes.
- `app.py`: worker, per-clinic settings/state, recent sync modes, job status.
- `clinic_refresh.py`: shared manual/automatic job coordinator.
- `static/clinic-refresh.js` and `static/app.js`: passive status polling and
  draft protection.
- `static/settings.html`, `static/settings.js`, `static/styles.css`: controls
  for the clinic interval and enabled status.
- `static/index.html`, `static/tasks.html`: cache-version updates.
- `tests/test_periodic_sync.py`, `tests/test_clinic_refresh.cjs`,
  `tests/serve_periodic_preview.py`: automated checks and isolated preview.

Other pre-existing working-tree changes are not part of this task. Publishing
must include only the files and app.py changes belonging to this feature.

Kommo filter references: [leads](https://developers.kommo.com/reference/leads-list)
and [events](https://developers.kommo.com/reference/events-list).
