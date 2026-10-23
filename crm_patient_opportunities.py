"""Commercial negotiations inside a patient card; never move the patient journey."""
import hashlib
import json
import re
import time
import uuid

from crm_access import pipeline_visible
from crm_store import Conflict, integer, text

STATUSES = {"open": "Aberta", "negotiation": "Negociação de Orçamento",
            "won": "Convertida", "lost": "Encerrada"}


def schema(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS patient_opportunities(
            id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),
            title TEXT NOT NULL,value_cents INTEGER NOT NULL,status TEXT NOT NULL,
            note TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,updated_at REAL NOT NULL,
            created_by TEXT NOT NULL,request_id TEXT NOT NULL UNIQUE,fingerprint TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS patient_opportunities_lead ON patient_opportunities(lead_id,created_at);
        CREATE TABLE IF NOT EXISTS patient_opportunity_events(
            id INTEGER PRIMARY KEY,opportunity_id TEXT NOT NULL REFERENCES patient_opportunities(id),
            actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,created_at REAL NOT NULL,
            before_json TEXT NOT NULL,after_json TEXT NOT NULL);
    """)
    columns = {r[1] for r in c.execute("PRAGMA table_info(patient_opportunities)")}
    for name in ("reason_id", "origin"):
        if name not in columns:
            c.execute(f"ALTER TABLE patient_opportunities ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")


def parent(c, store, doctor, lead_id):
    lead = store.owned(c, "leads", lead_id, doctor)
    pipeline = store.owned(c, "pipelines", lead["pipeline_id"], doctor)
    from crm_clinic_journey import clinical
    if lead["deleted_at"] or not clinical(pipeline):
        raise ValueError("Abra a ficha de um paciente ativo em um funil da clínica.")
    return lead


def listing(store, doctor, lead_id):
    with store.db() as c:
        lead = parent(c, store, doctor, lead_id)
        rows = [dict(r) for r in c.execute("""SELECT o.*,l.doctor_id,l.pipeline_id,p.name pipeline_name,p.institute_key
            FROM patient_opportunities o JOIN leads l ON l.id=o.lead_id JOIN pipelines p ON p.id=l.pipeline_id
            WHERE l.contact_id=? AND l.doctor_id=? AND l.deleted_at=0
            ORDER BY o.created_at DESC,o.id""", (lead["contact_id"], doctor)) if pipeline_visible(dict(r))]
        # Do not expose receipts used by another operator to replay creation.
        for row in rows:
            row.pop("request_id")
            row.pop("fingerprint")
            from crm_clinic_journey import state
            row["commercial_eligible"] = state(c, row["lead_id"])["disposition"] != "noncommercial"
            row["events"] = [{"actor_name": e["actor_name"], "created_at": e["created_at"], "before": json.loads(e["before_json"]), "after": json.loads(e["after_json"])}
                for e in c.execute("SELECT actor_name,created_at,before_json,after_json FROM patient_opportunity_events WHERE opportunity_id=? ORDER BY id", (row["id"],))]
        opened = [r for r in rows if r["commercial_eligible"] and r["status"] in {"open", "negotiation"}]
        from crm_clinic_journey import REASONS
        return {"items": rows, "statuses": STATUSES, "reasons": [{"id": k, "name": v[0]} for k,v in REASONS.items() if v[1] == "closed"], "summary": {"open": len(opened),
            "estimated_open_cents": sum(r["value_cents"] for r in opened),
            "won": sum(r["commercial_eligible"] and r["status"] == "won" for r in rows)}}


def save(store, doctor, payload, actor):
    title = text(payload.get("title"))
    value = integer(payload.get("value_cents", 0), 0, 100_000_000)
    status = payload.get("status", "open")
    note = payload.get("note", "")
    if not isinstance(status, str) or status not in STATUSES or not isinstance(note, str) or len(note) > 1200:
        raise ValueError("Situação ou observação da oportunidade inválida.")
    note = note.strip()
    from crm_clinic_journey import validate_reason
    closure = validate_reason(payload.get("closure"), "closed") if status == "lost" else None
    reason = closure["reason_id"] if closure else ""
    if closure and closure["note"]:
        note = closure["note"]
    now = time.time()
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        lead = parent(c, store, doctor, payload.get("lead_id"))
        from crm_clinic_journey import state
        if state(c, lead["id"])["disposition"] == "noncommercial":
            raise ValueError("Reclassifique o cartão como comercial antes de criar uma negociação.")
        before = {}
        ident = payload.get("id", "")
        if not isinstance(ident, str) or len(ident) > 64:
            raise ValueError("Identificador inválido.")
        if ident:
            row = c.execute("SELECT * FROM patient_opportunities WHERE id=? AND lead_id=?", (ident, lead["id"])).fetchone()
            if not row:
                raise LookupError()
            if integer(payload.get("revision"), 1, 2**31-1) != row["revision"]:
                raise Conflict("Esta oportunidade mudou. Atualize antes de salvar.")
            before = {key: row[key] for key in ("title", "value_cents", "status", "note", "reason_id")}
            c.execute("UPDATE patient_opportunities SET title=?,value_cents=?,status=?,note=?,reason_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                      (title, value, status, note, reason, now, ident))
        else:
            if payload.get("confirm_new_negotiation") is not True:
                raise ValueError("Confirme uma nova negociação, não uma sessão ou retorno já contratado.")
            request = payload.get("request_id")
            if not isinstance(request, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,64}", request):
                raise ValueError("Identificador da oportunidade inválido.")
            fingerprint = hashlib.sha256(json.dumps([lead["id"], title, value, status, note, reason]).encode()).hexdigest()
            receipt = c.execute("SELECT * FROM patient_opportunities WHERE request_id=?", (request,)).fetchone()
            if receipt:
                if (receipt["created_by"], receipt["fingerprint"]) != (str(actor["id"]), fingerprint):
                    raise Conflict("Solicitação já utilizada em outra oportunidade.")
                return receipt["id"]
            if integer(payload.get("patient_revision"), 1, 2**31-1) != lead["revision"]:
                raise Conflict("O cartão do paciente mudou. Atualize a ficha antes de criar a oportunidade.")
            ident = uuid.uuid4().hex
            c.execute("""INSERT INTO patient_opportunities(id,lead_id,title,value_cents,status,note,
                created_at,updated_at,created_by,request_id,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (ident, lead["id"], title, value, status, note, now, now, str(actor["id"]), request, fingerprint))
            c.execute("UPDATE patient_opportunities SET reason_id=? WHERE id=?", (reason, ident))
        after = {"title": title, "value_cents": value, "status": status, "note": note, "reason_id": reason}
        c.execute("INSERT INTO patient_opportunity_events(opportunity_id,actor_id,actor_name,created_at,before_json,after_json) VALUES(?,?,?,?,?,?)",
                  (ident, str(actor["id"]), actor["nome"], now, json.dumps(before), json.dumps(after)))
        # No stage event, XP, message, consent or campaign mutation is emitted here.
        return ident


def seed_legacy(c, lead, mapping, actor, now):
    """Preserve the previous card's commercial context once, without inventing a sale."""
    request = "legacy-" + lead["id"]
    if c.execute("SELECT 1 FROM patient_opportunities WHERE request_id=? OR lead_id=?", (request, lead["id"])).fetchone():
        return
    status = {"patients": "won", "closed": "lost", "negotiation": "negotiation"}.get(mapping["target"], "open")
    ident = uuid.uuid4().hex
    note = mapping["evidence"]
    c.execute("""INSERT INTO patient_opportunities(id,lead_id,title,value_cents,status,note,created_at,updated_at,
        created_by,request_id,fingerprint,reason_id,origin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ident, lead["id"], lead["title"], lead["value_cents"], status, note, lead["created_at"], now,
         str(actor["id"]), request, "legacy", mapping["reason_id"], "legacy_card"))
    c.execute("INSERT INTO patient_opportunity_events(opportunity_id,actor_id,actor_name,created_at,before_json,after_json) VALUES(?,?,?,?,?,?)",
        (ident, str(actor["id"]), actor["nome"], now, "{}", json.dumps({"title": lead["title"], "value_cents": lead["value_cents"], "status": status, "note": note, "origin": "legacy_card"})))
