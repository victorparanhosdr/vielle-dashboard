"""Audited active phone changes; conversation destinations remain historical facts."""
import re
import time

import phonenumbers

from crm_access import pipeline_visible


def schema(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS contact_phone_settings(
            contact_id TEXT PRIMARY KEY REFERENCES contacts(id),phone TEXT NOT NULL,
            revision INTEGER NOT NULL,changed_at REAL NOT NULL,changed_by TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contact_phone_edits(
            id INTEGER PRIMARY KEY,contact_id TEXT NOT NULL REFERENCES contacts(id),
            lead_id TEXT NOT NULL REFERENCES leads(id),old_phone TEXT NOT NULL,new_phone TEXT NOT NULL,
            actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,created_at REAL NOT NULL);
    """)


def normalize(value):
    if not isinstance(value, str) or len(value) > 40 or re.search(r"[^\d\s()+.-]", value):
        raise ValueError("Informe um telefone valido com DDD e codigo do pais.")
    digits = re.sub(r"\D", "", value)
    candidate = "+" + digits if not value.lstrip().startswith("+") and digits.startswith("55") and len(digits) in {12, 13} else value
    try:
        parsed = phonenumbers.parse(candidate, "BR")
    except phonenumbers.NumberParseException:
        raise ValueError("Confira o pais, o DDD e o telefone informado.") from None
    if not phonenumbers.is_valid_number(parsed) or parsed.extension:
        raise ValueError("Confira o pais, o DDD e o telefone informado.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)[1:]


def decorate(c, item):
    setting = c.execute("SELECT * FROM contact_phone_settings WHERE contact_id=?", (item.get("contact_id") or item["id"],)).fetchone()
    item["phone_revision"] = setting["revision"] if setting else 1
    item["phone_removed"] = bool(setting and not setting["phone"])
    if setting:
        item["phone"] = setting["phone"]


def attach(c, items):
    settings = {r["contact_id"]: r for r in c.execute("SELECT * FROM contact_phone_settings")}
    for item in items:
        setting = settings.get(item["contact_id"])
        item["phone_revision"] = setting["revision"] if setting else 1
        item["phone_removed"] = bool(setting and not setting["phone"])
        if setting:
            item["phone"] = setting["phone"]


def editable(c, contact):
    return all(pipeline_visible(dict(r)) for r in c.execute("SELECT p.* FROM leads l JOIN pipelines p ON p.id=l.pipeline_id WHERE l.contact_id=?", (contact,)))


def save(store, payload, actor):
    from crm_store import Conflict, integer
    from crm_identity import contacts_for
    revision = integer(payload.get("phone_revision"), 1, 2**63-1)
    if payload.get("confirm") is not True or type(payload.get("remove", False)) is not bool:
        raise ValueError("Confirme a alteracao do telefone.")
    number = "" if payload.get("remove") else normalize(payload.get("phone"))
    now = time.time()
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        lead = store.owned(c, "leads", payload.get("id"), payload.get("doctor_id"))
        contact = store.row(c, "contacts", lead["contact_id"])
        if not editable(c, contact["id"]):
            raise PermissionError("Este contato e compartilhado com funis fora do seu acesso. Solicite a alteracao ao Master.")
        if contact["phone_revision"] != revision:
            raise Conflict("O telefone foi alterado por outra pessoa. Atualize a ficha.")
        if contact["phone"] == number:
            return lead["id"]
        if c.execute("SELECT 1 FROM messages WHERE contact_id=? AND status='sending'", (contact["id"],)).fetchone():
            raise Conflict("Aguarde o envio em andamento antes de alterar o telefone.")
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_calls'").fetchone() and c.execute("SELECT 1 FROM voice_calls WHERE contact_id=? AND status NOT IN ('completed','failed','busy','no-answer','canceled','cancelled','declined')", (contact["id"],)).fetchone():
            raise Conflict("Finalize a ligacao em andamento antes de alterar o telefone.")
        if number:
            if any(row["id"] != contact["id"] for row in contacts_for(c, number)):
                raise Conflict("Este telefone ja pertence a outro cadastro. Confira os contatos; nenhum historico foi combinado.")
            match = store.patient_lookup(number)
            if contact["experts_uuid"] and match.get("status") == "matched" and match.get("uuid") != contact["experts_uuid"]:
                raise Conflict("O telefone corresponde a outro paciente no Clinica Experts. Confira antes de salvar.")
            if match.get("status") == "matched" and c.execute("SELECT 1 FROM contacts WHERE experts_uuid=? AND id<>?", (match.get("uuid"), contact["id"])).fetchone():
                raise Conflict("Este paciente ja possui outro cadastro. Confira antes de salvar.")
            c.execute("UPDATE contacts SET phone=? WHERE id=?", (number, contact["id"]))
            c.execute("INSERT OR IGNORE INTO contact_phones VALUES(?,?)", (number, contact["id"]))
        c.execute("""INSERT INTO contact_phone_settings VALUES(?,?,?,?,?) ON CONFLICT(contact_id)
            DO UPDATE SET phone=excluded.phone,revision=excluded.revision,changed_at=excluded.changed_at,changed_by=excluded.changed_by""",
            (contact["id"], number, revision + 1, now, str(actor["id"])))
        c.execute("INSERT INTO contact_phone_edits(contact_id,lead_id,old_phone,new_phone,actor_id,actor_name,created_at) VALUES(?,?,?,?,?,?,?)",
                  (contact["id"], lead["id"], contact["phone"], number, str(actor["id"]), actor["nome"], now))
        # Freeze old destinations; changing a phone must not retarget an existing queue.
        c.execute("UPDATE messages SET status='skipped',skip_reason='Telefone alterado ou removido; confira o destinatario' WHERE contact_id=? AND status='queued'", (contact["id"],))
        from crm_bots import BotStore
        for row in c.execute("SELECT id FROM leads WHERE contact_id=?", (contact["id"],)).fetchall():
            BotStore.cancel(c, "lead_id=?", (row["id"],), "Telefone alterado ou removido", now)
            c.execute("UPDATE bot_events SET processed=1 WHERE lead_id=?", (row["id"],))
            c.execute("UPDATE leads SET revision=revision+1,updated_at=? WHERE id=?", (now, row["id"]))
            store.event(c, row["id"], actor["nome"], "phone", "Telefone removido da ficha; historico preservado." if not number else "Telefone de contato atualizado; historico preservado.", now)
    return lead["id"]
