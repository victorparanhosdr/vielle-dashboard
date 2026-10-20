"""Conservative patient identity repair, retaining originals and audit evidence."""
import json
import os
import sqlite3
import threading
import time
import uuid

from crm_patients import phone_keys

BACKUP_LOCK = threading.Lock()


def backup(store):
    """One private pre-repair snapshot, in addition to row-level merge evidence."""
    with BACKUP_LOCK:
        path = store.path.parent / "backups" / "before-phone-identity-v1.sqlite3"
        if path.exists():
            return
        with sqlite3.connect(store.path) as source:
            if not source.execute("SELECT 1 FROM contacts LIMIT 1").fetchone():
                return
            path.parent.mkdir(mode=0o700, exist_ok=True)
            temporary = path.with_name(".phone-identity-" + uuid.uuid4().hex + ".sqlite3")
            try:
                with sqlite3.connect(temporary) as destination:
                    os.chmod(temporary, 0o600)
                    source.backup(destination)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)


def schema(c):
    if "merged_into" not in {r[1] for r in c.execute("PRAGMA table_info(contacts)")}:
        c.execute("ALTER TABLE contacts ADD COLUMN merged_into TEXT NOT NULL DEFAULT ''")
    c.execute("""CREATE TABLE IF NOT EXISTS contact_merges(
        source_id TEXT PRIMARY KEY REFERENCES contacts(id),target_id TEXT NOT NULL REFERENCES contacts(id),
        experts_uuid TEXT NOT NULL,created_at REAL NOT NULL,snapshot TEXT NOT NULL)""")


def contacts_for(c, number):
    keys = phone_keys(number)
    if not keys:
        return []
    return [dict(r) for r in c.execute("""SELECT DISTINCT ct.* FROM contacts ct
        JOIN contact_phones p ON p.contact_id=ct.id WHERE ct.merged_into=''
        AND p.phone IN (""" + ",".join("?" for _ in keys) + ")", keys)]


def merge(c, store, contacts, match):
    """Require a unique patient or the same complete, validated mobile pair."""
    phone_only = match.get("phone_only", False)
    if len(contacts) < 2 or (match["status"] != "matched" and not phone_only) or match.get("match_basis") == "phone_name":
        return None
    patient = match.get("uuid", "")
    manual_locations = {r["service_location"] for r in contacts if r["service_location_source"] == "manual"}
    if len(manual_locations) > 1:
        return None
    if any(c.execute("SELECT 1 FROM contact_phone_settings WHERE contact_id=?", (r["id"],)).fetchone() for r in contacts):
        return None
    for contact in contacts:
        if contact["experts_uuid"] and contact["experts_uuid"] != patient:
            return None
        numbers = [r[0] for r in c.execute("SELECT phone FROM contact_phones WHERE contact_id=?", (contact["id"],))]
        if phone_only:
            if not numbers or any(set(phone_keys(n)) != set(phone_keys(contacts[0]["phone"])) for n in numbers):
                return None
            if not match.get("course_only") and any(store.patient_lookup(n)["status"] != "new" for n in numbers):
                return None
        else:
            evidence = [store.patient_lookup(n) for n in numbers]
            if not evidence or any(m["status"] != "matched" or m["uuid"] != patient for m in evidence):
                return None
    # Distinct course purchasers sharing a phone must retain their own reviewable data.
    for pipeline in c.execute("SELECT id FROM pipelines WHERE course_key<>''"):
        emails = {r[0].strip().casefold() for ct in contacts for r in c.execute("SELECT email FROM leads WHERE contact_id=? AND pipeline_id=? AND email<>''", (ct["id"], pipeline[0]))}
        if len(emails) > 1:
            return None
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ids = [r["id"] for r in contacts]
    slots = ",".join("?" for _ in ids)
    if c.execute(f"SELECT 1 FROM messages WHERE contact_id IN ({slots}) AND status IN ('sending','unknown')", ids).fetchone():
        return None
    if "voice_calls" in tables and c.execute(f"SELECT 1 FROM voice_calls WHERE contact_id IN ({slots}) AND status NOT IN ('completed','failed','busy','no-answer','canceled','cancelled','declined')", ids).fetchone():
        return None
    ordered = sorted(contacts, key=lambda r: (c.execute("SELECT coalesce(min(created_at),1e30) FROM leads WHERE contact_id=?", (r["id"],)).fetchone()[0], r["id"]))
    target = ordered[0]
    now = time.time()
    from crm_bots import BotStore
    for source in ordered[1:]:
        old, new = source["id"], target["id"]
        # Keep complete pre-repair rows. No patient, lead, message or audio is deleted.
        snapshot = {"contacts": [dict(c.execute("SELECT * FROM contacts WHERE id=?", (i,)).fetchone()) for i in (old, new)]}
        for table, column in (("leads", "contact_id"), ("messages", "contact_id"), ("contact_phones", "contact_id"),
                              ("conversation_reads", "contact_id"), ("conversation_resolutions", "contact_id"),
                              ("contact_tags", "contact_id"), ("message_media", "contact_id"), ("voice_calls", "contact_id")):
            if table in tables:
                snapshot[table] = [dict(r) for r in c.execute(f"SELECT * FROM {table} WHERE {column} IN (?,?)", (old, new))]
        lead_ids = [r["id"] for r in snapshot["leads"]]
        lead_slots = ",".join("?" for _ in lead_ids)
        for table in ("events", "xp", "lead_attributions", "form_responses", "bot_runs", "bot_events", "bot_tasks", "kommo_import_refs", "lead_transfers"):
            if table in tables and lead_ids:
                snapshot[table] = [dict(r) for r in c.execute(f"SELECT * FROM {table} WHERE lead_id IN ({lead_slots})", lead_ids)]
        c.execute("INSERT INTO contact_merges VALUES(?,?,?,?,?)", (old, new, patient, now, json.dumps(snapshot, ensure_ascii=False)))
        for row in c.execute("SELECT * FROM leads WHERE contact_id IN (?,?)", (old, new)).fetchall():
            BotStore.cancel(c, "lead_id=?", (row["id"],), "Cadastro consolidado pelo telefone completo", now)
            c.execute("UPDATE bot_events SET processed=1 WHERE lead_id=?", (row["id"],))
        c.execute("UPDATE messages SET status='skipped',skip_reason='Cadastro consolidado; confira o destinatario antes de reenviar' WHERE contact_id IN (?,?) AND status='queued'", (old, new))
        for row in c.execute("SELECT * FROM leads WHERE contact_id=?", (old,)).fetchall():
            existing = c.execute("SELECT * FROM leads WHERE contact_id=? AND pipeline_id=?", (new, row["pipeline_id"])).fetchone()
            if existing:
                # Retain the original opportunity/stage. The newer duplicate remains in the trash.
                original = min((row, existing), key=lambda r: (bool(r["deleted_at"]), r["created_at"], r["id"]))
                keep, duplicate = existing, row
                if original["id"] != keep["id"]:
                    c.execute("""UPDATE leads SET stage_id=?,title=?,value_cents=?,next_action=?,created_at=?,
                        consent_at=?,consent_source=?,email=?,source_name=?,deleted_at=?,deleted_by=? WHERE id=?""",
                        tuple(original[k] for k in ("stage_id", "title", "value_cents", "next_action", "created_at",
                              "consent_at", "consent_source", "email", "source_name", "deleted_at", "deleted_by")) + (keep["id"],))
                c.execute("UPDATE leads SET deleted_at=?,deleted_by='Consolidacao de cadastro',revision=revision+1 WHERE id=?", (now, duplicate["id"]))
                for table in ("events", "xp", "lead_attributions", "form_responses", "bot_runs", "bot_events", "bot_tasks", "voice_calls", "kommo_import_refs", "lead_transfers"):
                    if table in tables:
                        c.execute(f"UPDATE {table} SET lead_id=? WHERE lead_id=?", (keep["id"], duplicate["id"]))
                c.execute("UPDATE leads SET revision=revision+1 WHERE id=?", (keep["id"],))
                store.event(c, keep["id"], "Sistema", "identity", "Cadastro duplicado consolidado pelo telefone completo; etapa original preservada. Outra oportunidade: " + duplicate["id"], now)
            else:
                c.execute("UPDATE leads SET contact_id=?,revision=revision+1 WHERE id=?", (new, row["id"]))
                store.event(c, row["id"], "Sistema", "identity", "Cadastro consolidado pelo telefone completo; historico preservado.", now)
        # Keep both sent messages if the two numbers appeared in one campaign.
        for message in c.execute("SELECT id,campaign_id FROM messages WHERE contact_id=? AND campaign_id IS NOT NULL", (old,)).fetchall():
            if c.execute("SELECT 1 FROM messages WHERE contact_id=? AND campaign_id=?", (new, message["campaign_id"])).fetchone():
                c.execute("UPDATE messages SET campaign_id=NULL WHERE id=?", (message["id"],))
        for table in ("messages", "message_media", "voice_calls", "lead_transfers"):
            if table in tables:
                c.execute(f"UPDATE {table} SET contact_id=? WHERE contact_id=?", (new, old))
        for read in c.execute("SELECT * FROM conversation_reads WHERE contact_id=?", (old,)).fetchall():
            c.execute("""INSERT INTO conversation_reads VALUES(?,?,?,?) ON CONFLICT(channel_id,contact_id,user_id)
                DO UPDATE SET last_read=min(last_read,excluded.last_read)""", (read["channel_id"], new, read["user_id"], read["last_read"]))
        c.execute("DELETE FROM conversation_reads WHERE contact_id=?", (old,))
        # A unified thread must not inherit a close marker that hides an unanswered message.
        c.execute("DELETE FROM conversation_resolutions WHERE contact_id IN (?,?)", (old, new))
        for tag in c.execute("SELECT * FROM contact_tags WHERE contact_id=?", (old,)).fetchall():
            c.execute("INSERT OR IGNORE INTO contact_tags VALUES(?,?,?,?)", (tag["doctor_id"], new, tag["label"], tag["created_at"]))
        c.execute("DELETE FROM contact_tags WHERE contact_id=?", (old,))
        c.execute("UPDATE contact_phones SET contact_id=? WHERE contact_id=?", (new, old))
        c.execute("UPDATE contacts SET opted_out=max(opted_out,?) WHERE id=?", (source["opted_out"], new))
        if source["service_location_source"] == "manual" and target["service_location_source"] != "manual":
            c.execute("UPDATE contacts SET service_location=?,service_location_source='manual',service_location_revision=service_location_revision+1 WHERE id=?", (source["service_location"], new))
        c.execute("UPDATE contacts SET experts_uuid='',experts_status='review',merged_into=? WHERE id=?", (new, old))
    return dict(c.execute("SELECT * FROM contacts WHERE id=?", (target["id"],)).fetchone())
