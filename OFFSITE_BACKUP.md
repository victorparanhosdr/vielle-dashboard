# External encrypted backups: local implementation, not activated

This is NOT a recovery guarantee or an approved production release. Native Railway backups are separate. Local startup/scheduling integration and runtime tooling have been prepared, but no production variable, live data, release, or Google account binding has been changed. Synthetic recovery passed; recovery of a real archive remains unverified.

## Data flow

1. Snapshot SQLite databases through the Online Backup API; never copy live database bytes without their committed WAL contents.
2. Check database integrity, foreign keys, and known ready attachments. Keep transient snapshots in a private directory; clean them after export.
3. Stream the archive to the official age executable. Only ciphertext is eligible for upload. Include file hashes and paths inside the encrypted manifest, not the external receipt.
4. Use one explicitly configured Google account. Read its existing OAuth row and cipher key server-side without constructing FormStore or rewriting shared account/queue state. Never extract browser or connector credentials.
5. Create an owner-only Drive folder through the same OAuth app and persist a pre-generated ID before mutation. Use a separate folder from conversation backups.
6. Persist the ciphertext, file ID, and resumable session in a private journal. An interrupted upload queries Google's received range or recognizes a completed file; it does not blindly duplicate a write.
7. Check remote size, MD5, optional provider SHA-256, expected folder, owner, and sharing state. A receipt says `uploaded_checksum_verified`, never `restore_verified`.

The public age recipient belongs on the server. The PRIVATE recovery identity does not. It is required only in a separately authorized, isolated restoration process and must not be transmitted to this app, the backup folder, logs, source control, or project memory.

## Runtime controls

The command and startup worker are disabled by default. Running without `DOC4DOCS_OFFSITE_BACKUP_ENABLED=1` returns disabled without filesystem/provider side effects. `app.py` starts a dedicated backup thread only when explicitly enabled. Do not enable it against production until release review and exact account binding are complete. `railpack.json` adds the official Debian age runtime package without replacing generated runtime packages.

Configuration names, without values:

- `DATA_DIR`: explicit absolute volume path.
- `DOC4DOCS_OFFSITE_BACKUP_ENABLED`: absent or zero until activation is authorized.
- `DOC4DOCS_OFFSITE_RECIPIENT`: public age recipient only.
- `DOC4DOCS_OFFSITE_GOOGLE_ACCOUNT_ID`: one exact existing account row; no first-account fallback.
- `DOC4DOCS_OFFSITE_GOOGLE_OWNER`: expected verified owner identity; keep out of public documentation.
- `CRM_GOOGLE_CLIENT_ID`, `CRM_GOOGLE_CLIENT_SECRET`: existing server-side OAuth application, not connector tokens.
- `DOC4DOCS_OFFSITE_AGE_BIN`: reviewed official age binary available at runtime.
- `DOC4DOCS_OFFSITE_MAX_BYTES`: default two GiB, bounded configuration.
- `DOC4DOCS_OFFSITE_BACKUP_HOUR`: server schedule hour in America/Sao_Paulo, default 3.

The owned `.offsite-backup` directory is excluded from capture. A nonblocking file lock prevents overlapping jobs. A minimum interval of twenty-three hours after success allows the next daily slot despite upload duration. The first enabled execution starts immediately; later executions use the configured daily hour. A missed slot is caught up after restart. Failed attempts use bounded exponential backoff; six failures require review. An uncertain active job blocks creating another snapshot and resumes the same ciphertext, file ID, and upload session.

No Drive file is deleted or trashed: retention currently means all copies, with storage growth to be monitored. There is no automatic retention deletion. Failures are shown in the Master status page and fixed-code server warnings; external email/push delivery has not been implemented.

Syntax for an explicit, already reviewed one-shot server job:

```sh
python offsite_backup_service.py run-once
```

This command has NOT been run against a real volume. Enabling/scheduling requires a deployment/restart impact explanation and specific authorization. The production routine runs on the server and does not depend on a Mac being on. Production publication, account binding, first encrypted upload/checksum confirmation, and first real recovery validation remain pending.

## Master status

`GET /api/master/backups` is Master-only and returns fixed operational fields, never archives, OAuth credentials, recovery identities, or data from the volume. `POST` requires an existing Master session, same-origin protection, a bounded JSON body, and exact boolean confirmation. It queues a server-side retry and writes an audit event; it cannot enable the routine or change the destination/key. The `/master/backups.html` page shows last upload, next attempt, stale/failure notices, private folder link, and retention. The real-recovery indicator deliberately remains unconfirmed; a provider checksum is not a recovery test.

## Test isolation

The initial native macOS harness did not establish complete bounds and was not used to validate the product. With user authorization, official Podman 6.1.4 was unpacked and its published hash, Developer ID signatures, and Apple notarization were checked. A disposable AppleHV VM was prepared with two CPUs, two GiB memory, a twelve GiB disk, no swap, and no home/credential mounts. The trusted toolchain was provisioned separately from application execution.

Fifty-eight synthetic tests passed in the local Podman container. They cover archive recovery/WAL, tamper and wrong keys, missing media, file/disk limits, private destination, Google identity, interrupted/completed uploads, explicit OAuth selection/revocation, journal resumption, disabled defaults, locking, missed daily slots, bounded retries, Master-only status, CSRF, and audit requests. One integrated fixture encrypts a real synthetic SQLite snapshot through the service, simulates Drive storage, and decrypts/validates the resulting archive.

Actual test tool versions: Python 3.13.17, SQLite 3.46.1, age 1.2.1, cryptography 50.0.2. Exact local image: `sha256:1c5a6e0f75de39fce5381f18f096c0fe99137bf0fda7659919ef45657a5236aa`. No real Google account, production database, or private recovery identity was mounted or used. Browser verification of the new Master page is a separate check, not included in that count.

The separate synthetic browser check passed at desktop and phone sizes (1440x1000 and 390x844). It checked disabled defaults, status refresh, confirmation before retry, no fabricated real-recovery success, suspended-review messaging, no horizontal overflow, and no browser JavaScript errors. Chromium Headless Shell 156.0.8078.47 ran inside the same disposable VM with external network disabled, a read-only source/toolchain, synthetic loopback-only HTTP, an empty allowlisted environment, and scratch-only writes. Browser bounds: one CPU, 512 MiB memory/no extra swap, 128 processes, 1024 descriptors, 16 MiB per file for bounded graphics buffers, 64 MiB scratch, 45 CPU seconds, no core files, and 120 seconds host wall time. This does not change the stricter backup test bounds. No screenshot file, browser profile, or generated fixture was retained outside the sandbox.

`tools/run_offsite_backup_tests_container.py` requires an already installed local Docker daemon and an already provisioned, reviewed Linux image. It does not download images or install dependencies. The image must include Python 3.13 at `/usr/local/bin/python3`, official age/keygen at `/usr/bin`, and the project's cryptography dependency. Provision and verify the toolchain separately before execution. Use an exact local image ID, not a moving tag.

```sh
python tools/run_offsite_backup_tests_container.py \
  --image sha256:REPLACE_WITH_VERIFIED_LOCAL_IMAGE_ID \
  --socket /ABSOLUTE/PATH/TO/LOCAL/docker.sock
```

Only six new modules, their six synthetic test files, and a trusted entry point are mounted read-only. The rest of the repository, `.env`, host home, private keys, databases, runtime socket, and external services are not mounted into the container. Network is disabled; root filesystem is read-only; writes go only to a thirty-two MiB scratch tmpfs. Limits: half CPU, 256 MiB memory with no extra swap, thirty-two processes, thirty CPU seconds, sixty-four descriptors, one MiB per file, no core dumps, and bounded wall time/output. The entry point checks the environment, resource limits, cgroup v2 bounds, scratch size, and network routes before importing target tests. The trusted host runner forcibly removes the container on exit or timeout. No target-generated file is retained or promoted as an artifact.

These passing synthetic checks do not authorize testing on the live CRM or prove a real backup can be recovered. Stop the disposable local VM after each validation session.

## Recovery

Run recovery in a new isolated directory without network, application imports, environment credentials, or workers. Provide a dummy identity for synthetic tests. Any later recovery of a real backup requires a separate plan for clinical-data access and secure user-controlled handling of the actual private identity. `check-restore` authenticates the complete age stream, rejects unsafe TAR members, enforces bounds, and compares the encrypted manifest against extracted files and SQLite integrity. It never starts the application or restores directly over a live volume.

```sh
python offsite_backup.py check-restore --source /scratch/example.age \
  --identity /scratch/dummy-identity.txt --output /scratch/new-restore --age /usr/bin/age
```

After any real recovery, campaign/send workers must remain disabled until separately reviewed. Do not infer that a saved queue may be replayed automatically.

## Official references

- https://www.sqlite.org/backup.html
- https://github.com/FiloSottile/age
- https://developers.google.com/workspace/drive/api/guides/manage-uploads
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/generateIds
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files
