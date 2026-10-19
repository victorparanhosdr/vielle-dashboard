"""Isolated Vielle CRM pilot. No writes to Kommo or clinical records."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
import uuid
from zoneinfo import ZoneInfo
from crm_filters import appointment_active, appointment_ready, financial_ready, matches, ordered, parse_filters

TZ = ZoneInfo("America/Sao_Paulo")
FOOTER = "\n\nPara não receber mais mensagens, responda SAIR."
CAMPAIGN_DAILY_LIMIT = 200
SERVICE_LOCATIONS = {"capelinha": "Capelinha", "belo-horizonte": "Belo Horizonte", "sao-paulo": "São Paulo"}
LOCATION_DDDS = {"33": "capelinha", "38": "capelinha", "31": "belo-horizonte", "11": "sao-paulo"}


def location_from_phone(number):
    number = re.sub(r"\D", "", number)
    if len(number) not in {12, 13} or not number.startswith("55"):
        return ""
    return LOCATION_DDDS.get(number[2:4], "")


def whatsapp_content(message):
    for _ in range(5):
        if not isinstance(message, dict):
            return ""
        extended = message.get("extendedTextMessage")
        body = message.get("conversation") or (extended.get("text") if isinstance(extended, dict) else "")
        if isinstance(body, str) and body:
            return body[:5000]
        for kind, label in (("imageMessage", "Imagem"), ("videoMessage", "Video"), ("audioMessage", "Audio"),
                            ("documentMessage", "Documento"), ("stickerMessage", "Figurinha"), ("locationMessage", "Localizacao"),
                            ("contactMessage", "Contato")):
            media = message.get(kind)
            if isinstance(media, dict):
                caption = media.get("caption") or media.get("fileName") or ""
                return (f"[{label}]" + (" " + caption if isinstance(caption, str) and caption else ""))[:5000]
        wrapper = next((message[k] for k in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage") if isinstance(message.get(k), dict)), {})
        message = wrapper.get("message")
    return ""


def whatsapp_time(value, now):
    if isinstance(value, bool):
        return 0
    try:
        stamp = float(value)
    except (ValueError, TypeError, OverflowError):
        return 0
    return stamp if math.isfinite(stamp) and 1262304000 <= stamp <= now + 300 else 0


def text(value, maximum=160, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError("Preencha os campos corretamente.")
    return value.strip()


def phone(value):
    if not isinstance(value, str) or re.search(r"[^\d\s()+.-]", value):
        raise ValueError("Informe o telefone completo, com codigo do pais.")
    number = re.sub(r"\D", "", value)
    if not 10 <= len(number) <= 15 or number.startswith("0"):
        raise ValueError("Informe o telefone completo, com codigo do pais.")
    return number


def integer(value, low, high):
    if isinstance(value, bool):
        raise ValueError("Valor numerico invalido.")
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Valor numerico invalido.") from None
    if str(result) != str(value) or not low <= result <= high:
        raise ValueError(f"Informe um valor entre {low} e {high}.")
    return result


def sender_title(name):
    # Keep the authenticated name on one line, without WhatsApp formatting delimiters.
    return text(" ".join(re.sub(r"[*_~`\x00-\x1f\x7f]", " ", text(name)).split()))


def signed_message(body, name):
    return f"*{name}*\n\n{body}"


class Conflict(ValueError):
    pass


class CrmStore:
    def __init__(self, root, patient_lookup=None, commercial_lookup=None, appointment_source=None, commercial_source=None, *, patient_directory=None):
        self.patient_lookup = patient_lookup or (lambda number: {"status": "unchecked"})
        self.patient_directory = patient_directory
        self.commercial_lookup = commercial_lookup
        self.appointment_source = appointment_source
        self.commercial_source = commercial_source
        self.path = Path(root) / "crm.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS doctors(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS pipelines(id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL REFERENCES doctors(id),name TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,UNIQUE(doctor_id,name));
            CREATE TABLE IF NOT EXISTS stages(id TEXT PRIMARY KEY,pipeline_id TEXT NOT NULL REFERENCES pipelines(id),name TEXT NOT NULL,
                position INTEGER NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('open','won','lost')));
            CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL REFERENCES doctors(id),name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,instance TEXT NOT NULL UNIQUE,state TEXT NOT NULL DEFAULT 'disconnected',
                next_send_at REAL NOT NULL DEFAULT 0,daily_limit INTEGER NOT NULL DEFAULT 200);
            CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY,name TEXT NOT NULL,phone TEXT NOT NULL UNIQUE,
                opted_out INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS leads(id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL REFERENCES doctors(id),
                pipeline_id TEXT NOT NULL REFERENCES pipelines(id),stage_id TEXT NOT NULL REFERENCES stages(id),
                contact_id TEXT NOT NULL REFERENCES contacts(id),title TEXT NOT NULL,value_cents INTEGER NOT NULL DEFAULT 0,
                consent_at REAL,consent_source TEXT NOT NULL DEFAULT '',next_action TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,updated_at REAL NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
                UNIQUE(contact_id,pipeline_id));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),
                actor TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS xp(id INTEGER PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),
                doctor_id TEXT NOT NULL,actor TEXT NOT NULL,label TEXT NOT NULL,points INTEGER NOT NULL,
                unique_key TEXT NOT NULL UNIQUE,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL REFERENCES doctors(id),
                channel_id TEXT NOT NULL REFERENCES channels(id),name TEXT NOT NULL,body TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'draft',start_at REAL NOT NULL,
                hour_from INTEGER NOT NULL,hour_to INTEGER NOT NULL,created_at REAL NOT NULL,created_by TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,channel_id TEXT NOT NULL REFERENCES channels(id),
                contact_id TEXT NOT NULL REFERENCES contacts(id),campaign_id TEXT REFERENCES campaigns(id),
                body TEXT NOT NULL,direction TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',
                provider_id TEXT,created_at REAL NOT NULL,sent_at REAL,claimed_at REAL,
                UNIQUE(campaign_id,contact_id));
            CREATE UNIQUE INDEX IF NOT EXISTS message_provider ON messages(channel_id,provider_id) WHERE provider_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS leads_doctor ON leads(doctor_id,pipeline_id);
            CREATE INDEX IF NOT EXISTS messages_queue ON messages(status,campaign_id);
            CREATE INDEX IF NOT EXISTS messages_thread ON messages(channel_id,contact_id);
            CREATE INDEX IF NOT EXISTS messages_thread_time ON messages(channel_id,contact_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS conversation_reads(channel_id TEXT NOT NULL REFERENCES channels(id),
                contact_id TEXT NOT NULL REFERENCES contacts(id),user_id TEXT NOT NULL,last_read INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(channel_id,contact_id,user_id));
            CREATE TABLE IF NOT EXISTS reply_requests(request_id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES messages(id),
                user_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_resolutions(channel_id TEXT NOT NULL REFERENCES channels(id),
                contact_id TEXT NOT NULL REFERENCES contacts(id),resolved_through INTEGER NOT NULL,
                reason TEXT NOT NULL CHECK(reason IN ('replied','closed')),actor_id TEXT NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(channel_id,contact_id));
            CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY,value REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS customer_tier_rules(id INTEGER PRIMARY KEY CHECK(id=1),
                silver_cents INTEGER NOT NULL,gold_cents INTEGER NOT NULL,
                CHECK(silver_cents>0 AND gold_cents>silver_cents));
            INSERT OR IGNORE INTO customer_tier_rules VALUES(1,500000,1500000);
            """)
            columns = {r[1] for r in c.execute("PRAGMA table_info(contacts)")}
            for name, definition in (("experts_uuid", "TEXT NOT NULL DEFAULT ''"),
                                     ("experts_status", "TEXT NOT NULL DEFAULT 'unchecked'"),
                                     ("experts_checked_at", "REAL NOT NULL DEFAULT 0"),
                                     ("service_location", "TEXT NOT NULL DEFAULT ''"),
                                     ("service_location_source", "TEXT NOT NULL DEFAULT 'pending'"),
                                     ("service_location_revision", "INTEGER NOT NULL DEFAULT 1")):
                if name not in columns:
                    c.execute(f"ALTER TABLE contacts ADD COLUMN {name} {definition}")
            columns = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
            for name in ("recipient_phone", "sender_name", "sender_user_id"):
                if name not in columns:
                    c.execute(f"ALTER TABLE messages ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            if "skip_reason" not in columns:
                c.execute("ALTER TABLE messages ADD COLUMN skip_reason TEXT NOT NULL DEFAULT ''")
            for name, definition in (("origin", "TEXT NOT NULL DEFAULT 'crm'"), ("provider_timestamp", "REAL NOT NULL DEFAULT 0")):
                if name not in columns:
                    c.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")
            columns = {r[1] for r in c.execute("PRAGMA table_info(conversation_resolutions)")}
            if "external_reply_at" not in columns:
                c.execute("ALTER TABLE conversation_resolutions ADD COLUMN external_reply_at REAL NOT NULL DEFAULT 0")
            columns = {r[1] for r in c.execute("PRAGMA table_info(campaigns)")}
            for name, definition in (("weekdays", "TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]'"),
                ("minute_from", "INTEGER NOT NULL DEFAULT 0"), ("minute_to", "INTEGER NOT NULL DEFAULT 0"),
                ("tag", "TEXT NOT NULL DEFAULT ''"), ("selected_count", "INTEGER NOT NULL DEFAULT 0"),
                ("duplicates_count", "INTEGER NOT NULL DEFAULT 0"), ("excluded_count", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    c.execute(f"ALTER TABLE campaigns ADD COLUMN {name} {definition}")
            c.executescript("""
                CREATE TABLE IF NOT EXISTS contact_tags(doctor_id TEXT NOT NULL REFERENCES doctors(id),
                    contact_id TEXT NOT NULL REFERENCES contacts(id),label TEXT NOT NULL,created_at REAL NOT NULL,
                    PRIMARY KEY(doctor_id,contact_id,label));
                CREATE TABLE IF NOT EXISTS campaign_audiences(id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,expires_at REAL NOT NULL,
                    campaign_id TEXT NOT NULL DEFAULT '');
            """)
            columns = {r[1] for r in c.execute("PRAGMA table_info(reply_requests)")}
            for name, definition in (("source_channel_id", "TEXT NOT NULL DEFAULT ''"),
                                     ("inbound_cursor", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    c.execute(f"ALTER TABLE reply_requests ADD COLUMN {name} {definition}")
            c.executescript("""
                CREATE UNIQUE INDEX IF NOT EXISTS contact_experts ON contacts(experts_uuid) WHERE experts_uuid<>'';
                CREATE TABLE IF NOT EXISTS contact_phones(phone TEXT PRIMARY KEY,contact_id TEXT NOT NULL REFERENCES contacts(id));
                INSERT OR IGNORE INTO contact_phones SELECT phone,id FROM contacts;
            """)
            # Raise existing numbers once, without resetting usage or campaign state.
            changed = c.execute("INSERT OR IGNORE INTO runtime(key,value) VALUES('campaign_daily_limit_200_v1',1)")
            if changed.rowcount:
                c.execute("UPDATE channels SET daily_limit=? WHERE daily_limit<?",
                          (CAMPAIGN_DAILY_LIMIT, CAMPAIGN_DAILY_LIMIT))
            changed = c.execute("INSERT OR IGNORE INTO runtime(key,value) VALUES('service_locations_v1',1)")
            if changed.rowcount:
                for contact in c.execute("SELECT id,phone FROM contacts WHERE service_location_source<>'manual'").fetchall():
                    location = location_from_phone(contact["phone"])
                    c.execute("UPDATE contacts SET service_location=?,service_location_source=? WHERE id=?",
                              (location, "ddd" if location else "pending", contact["id"]))

    def contact(self, c, number, name):
        match = self.patient_lookup(number)
        current = c.execute("SELECT ct.* FROM contacts ct JOIN contact_phones p ON p.contact_id=ct.id WHERE p.phone=?", (number,)).fetchone()
        linked = c.execute("SELECT * FROM contacts WHERE experts_uuid=?", (match.get("uuid", ""),)).fetchone() if match["status"] == "matched" else None
        if not current:
            if linked:
                current = linked
            else:
                cid = uuid.uuid4().hex
                c.execute("INSERT INTO contacts(id,name,phone) VALUES(?,?,?)", (cid, name, number))
                current = c.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            c.execute("INSERT OR IGNORE INTO contact_phones VALUES(?,?)", (number, current["id"]))
        cid = current["id"]
        status = match["status"]
        if (linked and linked["id"] != cid) or (status == "matched" and current["experts_uuid"] and current["experts_uuid"] != match["uuid"]):
            status = "review"
        elif status == "matched":
            c.execute("UPDATE contacts SET name=?,experts_uuid=? WHERE id=?", (match["name"], match["uuid"], cid))
        elif current["experts_uuid"]:
            # A stale/unavailable snapshot must not silently turn a linked
            # patient into a new person or overwrite an established name.
            status = "unchecked" if status == "unchecked" else "review"
        c.execute("UPDATE contacts SET experts_status=?,experts_checked_at=? WHERE id=?", (status, time.time(), cid))
        if current["service_location_source"] != "manual":
            location = location_from_phone(current["phone"])
            if location != current["service_location"]:
                c.execute("""UPDATE contacts SET service_location=?,service_location_source=?,
                    service_location_revision=service_location_revision+1 WHERE id=?""",
                          (location, "ddd" if location else "pending", cid))
        return dict(c.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone())

    def reconcile_patients(self):
        with self.db() as c:
            contacts = c.execute("SELECT id,name,phone FROM contacts").fetchall()
            for contact in contacts:
                self.contact(c, contact["phone"], contact["name"])

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def row(c, table, key):
        # Table names are internal constants, never request values.
        r = c.execute(f"SELECT * FROM {table} WHERE id=?", (key,)).fetchone()
        if r is None:
            raise LookupError("Registro nao encontrado.")
        return dict(r)

    @staticmethod
    def owned(c, table, key, doctor):
        r = CrmStore.row(c, table, key)
        if r["doctor_id"] != doctor:
            raise ValueError("Este registro pertence a outro doutor.")
        return r

    def bootstrap(self, structure):
        """Idempotent explicit copy of names only, without customers or messages."""
        for name, pipelines in structure.items():
            with self.db() as c:
                c.execute("INSERT OR IGNORE INTO doctors VALUES(?,?)", (uuid.uuid4().hex, text(name)))
                doctor = c.execute("SELECT id FROM doctors WHERE name=?", (name,)).fetchone()[0]
            for pipeline in pipelines:
                with self.db() as c:
                    exists = c.execute("SELECT 1 FROM pipelines WHERE doctor_id=? AND name=?", (doctor, pipeline)).fetchone()
                if not exists:
                    self.pipeline({"doctor_id": doctor, "name": pipeline, "stages": [
                        {"name": n, "kind": k} for n, k in (("Novo contato", "open"), ("Em conversa", "open"),
                        ("Avaliacao agendada", "open"), ("Proposta enviada", "open"), ("Ganho", "won"), ("Perdido", "lost"))]})

    def doctor(self, payload):
        key, name = uuid.uuid4().hex, text(payload.get("name"))
        with self.db() as c:
            c.execute("INSERT INTO doctors VALUES(?,?)", (key, name))
        return key

    def pipeline(self, p):
        doctor, key = p.get("doctor_id"), p.get("id") or uuid.uuid4().hex
        stages = p.get("stages")
        if not isinstance(stages, list) or not 2 <= len(stages) <= 12:
            raise ValueError("Defina de 2 a 12 fases.")
        cleaned = [(s.get("id") or uuid.uuid4().hex, text(s.get("name"), 60), s.get("kind")) for s in stages if isinstance(s, dict)]
        if len(cleaned) != len(stages) or len({s[0] for s in cleaned}) != len(cleaned) or any(s[2] not in {"open", "won", "lost"} for s in cleaned):
            raise ValueError("Fases invalidas.")
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            self.row(c, "doctors", doctor)
            if p.get("id"):
                previous = self.owned(c, "pipelines", key, doctor)
                if previous["revision"] != p.get("revision"):
                    raise Conflict("O funil mudou. Atualize antes de salvar.")
                old = {r[0] for r in c.execute("SELECT id FROM stages WHERE pipeline_id=?", (key,))}
                for stage in old - {s[0] for s in cleaned}:
                    if c.execute("SELECT 1 FROM leads WHERE stage_id=?", (stage,)).fetchone():
                        raise ValueError("Mova os clientes antes de remover uma fase em uso.")
                    c.execute("DELETE FROM stages WHERE id=?", (stage,))
                c.execute("UPDATE pipelines SET name=?,revision=revision+1 WHERE id=?", (text(p.get("name")), key))
            else:
                c.execute("INSERT INTO pipelines(id,doctor_id,name) VALUES(?,?,?)", (key, doctor, text(p.get("name"))))
            for position, (sid, name, kind) in enumerate(cleaned):
                existing = c.execute("SELECT pipeline_id,kind FROM stages WHERE id=?", (sid,)).fetchone()
                if existing and existing[0] != key:
                    raise ValueError("Fase de outro funil.")
                if existing and existing[1] != kind and c.execute("SELECT 1 FROM leads WHERE stage_id=?", (sid,)).fetchone():
                    raise ValueError("Nao altere o resultado de uma fase em uso. Crie outra fase.")
                c.execute("INSERT INTO stages VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,position=excluded.position,kind=excluded.kind",
                          (sid, key, name, position, kind))
        return key

    def channel(self, p):
        key = uuid.uuid4().hex
        with self.db() as c:
            self.row(c, "doctors", p.get("doctor_id"))
            c.execute("INSERT INTO channels(id,doctor_id,name,phone,instance,daily_limit) VALUES(?,?,?,?,?,?)",
                      (key, p["doctor_id"], text(p.get("name")), phone(p.get("phone")), "doc4docs-vielle-" + key,
                       integer(p.get("daily_limit", CAMPAIGN_DAILY_LIMIT), 1, CAMPAIGN_DAILY_LIMIT)))
        return key

    def lead(self, p, actor):
        now, key = time.time(), p.get("id") or uuid.uuid4().hex
        doctor, pipeline, stage = p.get("doctor_id"), p.get("pipeline_id"), p.get("stage_id")
        due = p.get("next_action", "")
        if due:
            datetime.strptime(due, "%Y-%m-%d")
        value = integer(p.get("value_cents", 0), 0, 100_000_000)
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            self.owned(c, "pipelines", pipeline, doctor)
            target = self.row(c, "stages", stage)
            if target["pipeline_id"] != pipeline:
                raise ValueError("A fase nao pertence a este funil.")
            if p.get("id"):
                old = self.owned(c, "leads", key, doctor)
                contact = self.row(c, "contacts", old["contact_id"])
                self.contact(c, contact["phone"], contact["name"])
                if old["pipeline_id"] != pipeline:
                    raise ValueError("O cliente pertence a outro funil.")
                if old["revision"] != p.get("revision"):
                    raise Conflict("O cliente foi alterado por outra pessoa. Atualize e tente novamente.")
                c.execute("UPDATE leads SET title=?,stage_id=?,value_cents=?,next_action=?,updated_at=?,revision=revision+1 WHERE id=?",
                          (text(p.get("title")), stage, value, due, now, key))
                if old["stage_id"] != stage:
                    previous = self.row(c, "stages", old["stage_id"])
                    self.event(c, key, actor, "stage", target["name"], now)
                    if target["kind"] == "won" or (target["kind"] == "open" and previous["kind"] == "open" and target["position"] > previous["position"]):
                        self.award(c, key, doctor, actor, "Fechamento" if target["kind"] == "won" else "Avanco no funil",
                                   25 if target["kind"] == "won" else 5, key + (":won" if target["kind"] == "won" else ":stage:" + stage), now)
            else:
                number, name = phone(p.get("phone")), text(p.get("name"))
                selected_patient = p.get("experts_uuid", "")
                if selected_patient:
                    match = self.patient_lookup(number)
                    if match["status"] != "matched" or match.get("uuid") != selected_patient:
                        raise ValueError("Telefone e paciente nao conferem. Atualize a busca no Clinica Experts.")
                    name = match["name"]
                contact = self.contact(c, number, name)
                if selected_patient and (contact["experts_status"] != "matched" or contact["experts_uuid"] != selected_patient):
                    raise Conflict("Este telefone possui um vinculo diferente no CRM. Confira o cadastro antes de adicionar.")
                existing = c.execute("SELECT id FROM leads WHERE pipeline_id=? AND contact_id=?", (pipeline, contact["id"])).fetchone()
                if existing:
                    return existing["id"]
                consent = p.get("consent") is True
                source = text(p.get("consent_source", ""), 300, required=consent)
                c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,value_cents,consent_at,consent_source,next_action,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (key, doctor, pipeline, stage, contact["id"], text(p.get("title")), value, now if consent else None, source, due, now, now))
                self.event(c, key, actor, "created", "Oportunidade criada", now)
        return key

    @staticmethod
    def event(c, lead, actor, kind, body, now):
        c.execute("INSERT INTO events(lead_id,actor,kind,body,created_at) VALUES(?,?,?,?,?)", (lead, actor, kind, body, now))

    @staticmethod
    def award(c, lead, doctor, actor, label, points, unique, now):
        c.execute("INSERT OR IGNORE INTO xp(lead_id,doctor_id,actor,label,points,unique_key,created_at) VALUES(?,?,?,?,?,?,?)",
                  (lead, doctor, actor, label, points, unique, now))

    def activity(self, p, actor):
        now = time.time()
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            lead = self.owned(c, "leads", p.get("id"), p.get("doctor_id"))
            kind = p.get("kind", "note")
            if kind == "consent":
                source = text(p.get("body"), 300)
                contact = self.row(c, "contacts", lead["contact_id"])
                if contact["opted_out"]:
                    raise ValueError("Contato descadastrado. Use Reativar contato para corrigir um descadastro.")
                c.execute("UPDATE leads SET consent_at=?,consent_source=? WHERE id=?", (now, source, lead["id"]))
            elif kind == "optout":
                c.execute("UPDATE contacts SET opted_out=1 WHERE id=?", (lead["contact_id"],))
                c.execute("UPDATE messages SET status='skipped' WHERE contact_id=? AND status='queued'", (lead["contact_id"],))
            elif kind == "reactivate":
                body = text(p.get("body"), 2000)
                if p.get("confirm_reactivation") is not True:
                    raise ValueError("Confirme que o cliente deseja voltar a receber contato.")
                revision = integer(p.get("revision"), 1, 2**63-1)
                contact = self.row(c, "contacts", lead["contact_id"])
                if not contact["opted_out"] or lead["revision"] != revision:
                    raise Conflict("O cadastro foi alterado. Atualize a ficha antes de reativar.")
                # Never revive an old queue, including one not yet inspected by the worker.
                c.execute("UPDATE messages SET status='skipped',skip_reason='Descadastrado; reativacao nao retoma envios antigos' WHERE contact_id=? AND status='queued'", (contact["id"],))
                c.execute("UPDATE contacts SET opted_out=0 WHERE id=?", (contact["id"],))
                for row in c.execute("SELECT id FROM leads WHERE contact_id=?", (contact["id"],)).fetchall():
                    self.event(c, row["id"], actor, kind, "Cliente confirmou que deseja voltar a receber contato. Motivo: " + body, now)
                c.execute("UPDATE leads SET updated_at=?,revision=revision+1 WHERE contact_id=?", (now, contact["id"]))
                return
            elif kind not in {"note", "followup"}:
                raise ValueError("Atividade invalida.")
            body = text(p.get("body", "Descadastro registrado" if kind == "optout" else ""), 2000)
            self.event(c, lead["id"], actor, kind, body, now)
            if kind == "followup":
                day = datetime.fromtimestamp(now, TZ).date().isoformat()
                self.award(c, lead["id"], lead["doctor_id"], actor, "Follow-up registrado", 3, lead["id"] + ":followup:" + day, now)
            c.execute("UPDATE leads SET updated_at=?,revision=revision+1 WHERE id=?", (now, lead["id"]))

    def detail(self, key, doctor):
        with self.db() as c:
            lead = self.owned(c, "leads", key, doctor)
            contact = self.row(c, "contacts", lead["contact_id"])
            self.contact(c, contact["phone"], contact["name"])
            contact = self.row(c, "contacts", lead["contact_id"])
            lead.update({k: contact[k] for k in ("name", "phone", "opted_out", "experts_uuid", "experts_status", "experts_checked_at",
                                               "service_location", "service_location_source", "service_location_revision")})
            self.attach_appointments([lead], self.row(c, "doctors", doctor)["name"])
            self.attach_customer_values(c, [lead], self.row(c, "doctors", doctor)["name"])
            preferred = c.execute("""SELECT m.channel_id FROM messages m JOIN channels ch ON ch.id=m.channel_id
                WHERE m.contact_id=? AND ch.doctor_id=? AND m.direction='in' ORDER BY m.rowid DESC LIMIT 1""", (contact["id"], doctor)).fetchone()
            return {"lead": lead, "events": [dict(r) for r in c.execute("SELECT * FROM events WHERE lead_id=? ORDER BY id DESC LIMIT 100", (key,))],
                    "preferred_channel_id": preferred[0] if preferred else "",
                    "messages": [dict(r) for r in c.execute("SELECT m.*,ch.name channel_name FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.contact_id=? AND ch.doctor_id=? ORDER BY m.created_at DESC LIMIT 100", (contact["id"], doctor))]}

    def save_service_location(self, payload, actor):
        revision = integer(payload.get("service_location_revision"), 1, 2**63-1)
        auto = payload.get("use_ddd", False)
        location = payload.get("service_location", "")
        if type(auto) is not bool or not isinstance(location, str) or location not in {"", *SERVICE_LOCATIONS}:
            raise ValueError("Local de Atendimento invalido.")
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            lead = self.owned(c, "leads", payload.get("id"), payload.get("doctor_id"))
            contact = self.row(c, "contacts", lead["contact_id"])
            if contact["service_location_revision"] != revision:
                raise Conflict("O Local de Atendimento foi alterado. Atualize a ficha e tente novamente.")
            if auto:
                location = location_from_phone(contact["phone"])
            source = ("ddd" if location else "pending") if auto else "manual"
            if (contact["service_location"], contact["service_location_source"]) == (location, source):
                return
            c.execute("""UPDATE contacts SET service_location=?,service_location_source=?,
                service_location_revision=service_location_revision+1 WHERE id=?""", (location, source, contact["id"]))
            body = "Local de Atendimento: " + SERVICE_LOCATIONS.get(location, "Nao definido - pendente")
            body += " (regra do DDD)" if auto else " (definido manualmente)"
            now = time.time()
            for linked in c.execute("SELECT id FROM leads WHERE contact_id=?", (contact["id"],)).fetchall():
                self.event(c, linked["id"], actor, "service_location", body, now)

    def attach_appointments(self, leads, doctor_name):
        patients = list({l["experts_uuid"] for l in leads if l["experts_uuid"] and l["experts_status"] == "matched"})
        summaries = self.appointment_source.summaries(patients, doctor_name) if self.appointment_source and patients else {}
        for lead in leads:
            state = "unlinked" if lead["experts_status"] == "new" else "identity_pending"
            lead["appointment"] = summaries.get(lead["experts_uuid"], {"state": "unavailable" if lead["experts_status"] == "matched" else state, "item": None, "kind": None})

    def attach_appointment_filters(self, leads, doctor_name, filters):
        patients = list({l["experts_uuid"] for l in leads if l["experts_uuid"] and l["experts_status"] == "matched"})
        values = self.appointment_source.segments(patients, doctor_name, filters["appointment_status"],
            filters["appointment_from"], filters["appointment_to"]) if self.appointment_source and patients else {}
        for lead in leads:
            state = "unavailable" if lead["experts_status"] == "matched" else "unlinked" if lead["experts_status"] == "new" else "identity_pending"
            data = values.get(lead["experts_uuid"]) if lead["experts_status"] == "matched" else None
            data = data or {"state": state, "item": None, "count": 0, "has_upcoming": False, "kind": None}
            lead["appointment"] = data.get("summary", {"state": state, "item": None, "kind": None})
            lead["appointment_filter"] = {k: v for k, v in data.items() if k != "summary"}

    def save_customer_tier_rules(self, payload):
        silver = integer(payload.get("silver_cents"), 1, 10000000000)
        gold = integer(payload.get("gold_cents"), silver+1, 10000000001)
        with self.db() as c:
            c.execute("UPDATE customer_tier_rules SET silver_cents=?,gold_cents=? WHERE id=1", (silver, gold))

    @staticmethod
    def customer_tier_rules(c):
        return dict(c.execute("SELECT silver_cents,gold_cents FROM customer_tier_rules WHERE id=1").fetchone())

    def attach_customer_values(self, c, leads, doctor_name, include_search=False):
        rules = self.customer_tier_rules(c)
        patients = list({l["experts_uuid"] for l in leads if l["experts_uuid"] and l["experts_status"] == "matched"})
        summaries = self.commercial_source.summaries(patients, doctor_name, include_search=include_search) if self.commercial_source and patients else {}
        for lead in leads:
            fallback = "unlinked" if lead["experts_status"] == "new" else "identity_pending"
            data = summaries.get(lead["experts_uuid"], {"state": "unavailable" if lead["experts_status"] == "matched" else fallback}) if lead["experts_status"] == "matched" else {"state": fallback}
            result = {"state": data["state"], "tier": None, "scope": "doctor", "doctor_name": doctor_name, "synced_at": data.get("synced_at", 0)}
            history = {"sales_state": data["state"], "quotes_state": data["state"]}
            if data["state"] == "ready":
                total = data["summary"]["sales_cents"]
                history.update(data["summary"], sales_state="partial" if any(data.get("sales_warnings", {}).values()) else "ready",
                               quotes_state="partial" if any(data.get("quote_warnings", {}).values()) else "ready")
                result.update(sales_cents=total, sales_count=data["summary"]["sales_count"])
                if any(data.get("sales_warnings", {}).values()):
                    result["state"] = "partial"
                else:
                    tier = "gold" if total >= rules["gold_cents"] else "silver" if total >= rules["silver_cents"] else "bronze"
                    target = rules["gold_cents"] if tier == "silver" else rules["silver_cents"] if tier == "bronze" else None
                    result.update(tier=tier, next_tier="gold" if tier == "silver" else "silver" if tier == "bronze" else None,
                                  next_cents=target, remaining_cents=max(0, target-total) if target else 0)
            lead["customer_value"] = result
            lead["customer_history"] = history
            if include_search:
                lead["_experts_search"] = data.get("search_text", "")

    def appointments(self, key, doctor, scope="doctor", period="all", page=1):
        if scope not in {"doctor", "clinic"} or period not in {"all", "upcoming", "past"}:
            raise ValueError("Filtro invalido.")
        page = integer(page, 1, 100000)
        lead = self.detail(key, doctor)["lead"]
        if lead["experts_status"] != "matched" or not lead["experts_uuid"]:
            return {"state": "unlinked" if lead["experts_status"] == "new" else "identity_pending", "items": []}
        if self.appointment_source is None:
            return {"state": "unavailable", "items": []}
        with self.db() as c:
            name = self.row(c, "doctors", doctor)["name"]
        return self.appointment_source.history(lead["experts_uuid"], name, scope, period, page)

    def commercial(self, key, doctor, scope="doctor", kind="sales", page=1):
        if scope not in {"doctor", "clinic"} or kind not in {"sales", "quotes"}:
            raise ValueError("Filtro invalido.")
        page = integer(page, 1, 100000)
        detail = self.detail(key, doctor)
        contact = detail["lead"]
        if not contact["experts_uuid"] or contact["experts_status"] != "matched":
            return {"state": "unlinked" if contact["experts_status"] == "new" else "identity_pending", "items": [], "summary": {}}
        with self.db() as c:
            name = self.row(c, "doctors", doctor)["name"]
        if self.commercial_lookup is None:
            return {"state": "unavailable", "items": [], "summary": {}}
        return self.commercial_lookup(contact["experts_uuid"], name, scope, kind, page)

    def listing(self, doctor="", filters=None):
        filters = parse_filters(filters)
        self.reconcile_patients()
        with self.db() as c:
            doctors = [dict(r) for r in c.execute("SELECT * FROM doctors ORDER BY name")]
            if doctor:
                self.row(c, "doctors", doctor)
            doctor = doctor or (doctors[0]["id"] if doctors else "")
            pipelines = [dict(r) for r in c.execute("SELECT * FROM pipelines WHERE doctor_id=? ORDER BY name", (doctor,))]
            for p in pipelines:
                p["stages"] = [dict(r) for r in c.execute("SELECT * FROM stages WHERE pipeline_id=? ORDER BY position", (p["id"],))]
            leads = [dict(r) for r in c.execute("SELECT l.*,t.name,t.phone,t.opted_out,t.experts_uuid,t.experts_status,t.service_location,t.service_location_source,t.service_location_revision,s.kind FROM leads l JOIN contacts t ON t.id=l.contact_id JOIN stages s ON s.id=l.stage_id WHERE l.doctor_id=? ORDER BY l.updated_at DESC,l.id", (doctor,))]
            location_pending = {p["id"]: len({l["contact_id"] for l in leads if l["pipeline_id"] == p["id"] and not l["service_location"]}) for p in pipelines}
            self.attach_customer_values(c, leads, next((d["name"] for d in doctors if d["id"] == doctor), ""), bool(filters["search"]))
            if appointment_active(filters):
                self.attach_appointment_filters(leads, next((d["name"] for d in doctors if d["id"] == doctor), ""), filters)
            incomplete = len({l["contact_id"] for l in leads if not financial_ready(l, filters) or not appointment_ready(l, filters)})
            leads = ordered([l for l in leads if matches(l, filters)], filters)
            matched_count = len(leads)
            leads = leads[:1000]
            if not appointment_active(filters):
                self.attach_appointments(leads, next((d["name"] for d in doctors if d["id"] == doctor), ""))
            for lead in leads:
                lead.pop("_experts_search", None)
            campaigns = [dict(r) for r in c.execute("SELECT * FROM campaigns WHERE doctor_id=? ORDER BY created_at DESC LIMIT 100", (doctor,))]
            for campaign in campaigns:
                campaign["counts"] = {r[0]: r[1] for r in c.execute("SELECT status,count(*) FROM messages WHERE campaign_id=? GROUP BY status", (campaign["id"],))}
            today = datetime.now(TZ).date().isoformat()
            daily = datetime.combine(datetime.now(TZ).date(), datetime.min.time(), TZ).timestamp()
            xp = c.execute("SELECT coalesce(sum(points),0) FROM xp WHERE doctor_id=?", (doctor,)).fetchone()[0]
            team = [dict(r) for r in c.execute("SELECT actor,sum(points) points FROM xp WHERE doctor_id=? GROUP BY actor ORDER BY points DESC LIMIT 10", (doctor,))]
            return {"doctors": doctors, "doctor_id": doctor, "pipelines": pipelines, "leads": leads,
                    "customer_tier_rules": self.customer_tier_rules(c),
                    "service_locations": SERVICE_LOCATIONS, "service_location_pending": location_pending,
                    "matched_leads": matched_count, "filter_incomplete": incomplete,
                    "total_leads": c.execute("SELECT count(*) FROM leads WHERE doctor_id=?", (doctor,)).fetchone()[0],
                    "channels": [dict(r) for r in c.execute("SELECT * FROM channels WHERE doctor_id=? ORDER BY name", (doctor,))],
                    "campaigns": campaigns, "game": {"xp": xp, "level": xp // 100 + 1, "progress": xp % 100, "team": team,
                    "followups": c.execute("SELECT count(*) FROM xp WHERE doctor_id=? AND label='Follow-up registrado' AND created_at>=?", (doctor, daily)).fetchone()[0],
                    "advances": c.execute("SELECT count(*) FROM xp WHERE doctor_id=? AND label='Avanco no funil' AND created_at>=?", (doctor, daily)).fetchone()[0],
                    "wins": c.execute("SELECT count(*) FROM xp WHERE doctor_id=? AND label='Fechamento' AND created_at>=?", (doctor, daily)).fetchone()[0]}, "today": today}

    @staticmethod
    def conversation_contact(c, doctor, channel, contact):
        ch = CrmStore.owned(c, "channels", channel, doctor)
        ct = CrmStore.row(c, "contacts", contact)
        if not c.execute("SELECT 1 FROM leads WHERE doctor_id=? AND contact_id=? UNION ALL SELECT 1 FROM messages WHERE channel_id=? AND contact_id=? LIMIT 1",
                         (doctor, contact, channel, contact)).fetchone():
            raise ValueError("Conversa de outro doutor.")
        return ch, ct

    @staticmethod
    def conversation_peer(c, channel, contact, fallback):
        peer = c.execute("""SELECT recipient_phone FROM messages WHERE channel_id=? AND contact_id=? AND recipient_phone<>''
            AND (direction='in' OR status IN ('queued','sending','sent','unknown')) ORDER BY created_at DESC,rowid DESC LIMIT 1""", (channel, contact)).fetchone()
        return peer[0] if peer else fallback

    def inbox(self, doctor, user, channel="", kind="all", search="", filters=None):
        if kind not in {"all", "unread", "waiting"}:
            raise ValueError("Filtro invalido.")
        filters = parse_filters(filters)
        if search:
            filters["search"] = text(search, 160, False)
        with self.db() as c:
            doc = self.row(c, "doctors", doctor)
            if channel:
                self.owned(c, "channels", channel, doctor)
            # Campaign drafts are not conversations until dispatch has actually begun.
            rows = c.execute("""WITH visible AS (
                SELECT m.rowid seq,m.*,row_number() OVER(PARTITION BY m.channel_id,m.contact_id ORDER BY m.created_at DESC,m.rowid DESC) newest
                FROM messages m JOIN channels ch ON ch.id=m.channel_id
                WHERE ch.doctor_id=? AND (?='' OR ch.id=?) AND
                (m.direction='in' OR m.status IN ('sent','sending','unknown') OR (m.campaign_id IS NULL AND m.status='queued'))
            ), threads AS (
                SELECT channel_id,contact_id,max(CASE WHEN newest=1 THEN seq END) latest,
                sum(CASE WHEN direction='in' AND seq>coalesce((SELECT resolved_through FROM conversation_resolutions r
                    WHERE r.channel_id=visible.channel_id AND r.contact_id=visible.contact_id),0)
                    AND coalesce(nullif(provider_timestamp,0),created_at)>=coalesce((SELECT external_reply_at FROM conversation_resolutions r
                    WHERE r.channel_id=visible.channel_id AND r.contact_id=visible.contact_id),0) THEN 1 ELSE 0 END) unread
                FROM visible GROUP BY channel_id,contact_id
            ) SELECT m.*,t.unread,ct.name,ct.phone,ct.experts_status,ct.experts_uuid,ct.opted_out,
                ct.service_location,ct.service_location_source,ct.service_location_revision,ch.name channel_name,
                (SELECT id FROM leads WHERE doctor_id=? AND contact_id=ct.id ORDER BY updated_at DESC LIMIT 1) lead_id
                FROM threads t JOIN visible m ON m.seq=t.latest JOIN contacts ct ON ct.id=t.contact_id
                JOIN channels ch ON ch.id=t.channel_id ORDER BY m.created_at DESC,m.seq DESC""", (doctor, channel, channel, doctor)).fetchall()
            items = [dict(r) for r in rows]
            self.attach_customer_values(c, items, doc["name"], bool(filters["search"]))
            if appointment_active(filters):
                self.attach_appointment_filters(items, doc["name"], filters)
            titles = {r["contact_id"]: r["title"] for r in c.execute("SELECT contact_id,title FROM leads WHERE doctor_id=? ORDER BY updated_at,id", (doctor,))}
            for item in items:
                item["title"] = titles.get(item["contact_id"], "")
            incomplete = len({r["contact_id"] for r in items if not financial_ready(r, filters) or not appointment_ready(r, filters)})
            items = ordered([r for r in items if matches(r, filters, r["body"])], filters)
            for item in items:
                item.pop("_experts_search", None)
            counts = {"all": len(items), "unread": sum(r["unread"] > 0 for r in items),
                      "waiting": sum(r["unread"] > 0 for r in items)}
            items = [r for r in items if kind == "all" or r["unread"]]
            return {"conversations": items[:200], "total": len(items), "counts": counts, "filter_incomplete": incomplete}

    def conversation(self, doctor, channel, contact, before=0):
        before = integer(before, 0, 2**63-1)
        with self.db() as c:
            ch, ct = self.conversation_contact(c, doctor, channel, contact)
            boundary = c.execute("SELECT created_at FROM messages WHERE rowid=? AND channel_id=? AND contact_id=?", (before, channel, contact)).fetchone() if before else None
            if before and boundary is None:
                raise ValueError("Marcador de conversa invalido.")
            rows = c.execute("""SELECT rowid seq,* FROM messages WHERE channel_id=? AND contact_id=?
                AND (?=0 OR created_at<? OR (created_at=? AND rowid<?)) AND (direction='in' OR status IN ('sent','sending','unknown')
                OR (campaign_id IS NULL AND status='queued')) ORDER BY created_at DESC,rowid DESC LIMIT 81""",
                (channel, contact, before, boundary[0] if boundary else 0, boundary[0] if boundary else 0, before)).fetchall()
            messages = [dict(r) for r in reversed(rows[:80])]
            reply_phone = self.conversation_peer(c, channel, contact, ct["phone"])
            # Default WhatsApp availability is not evidence of patient consent.
            allowed = not ct["opted_out"]
            return {"contact": ct, "channel": ch, "messages": messages, "has_older": len(rows)>80,
                    "inbound_cursor": max((m["seq"] for m in messages if m["direction"] == "in"), default=0),
                    **self.conversation_status(c, channel, contact),
                    "reply_allowed": allowed, "reply_phone": reply_phone}

    @staticmethod
    def conversation_status(c, channel, contact):
        resolved = c.execute("SELECT * FROM conversation_resolutions WHERE channel_id=? AND contact_id=?", (channel, contact)).fetchone()
        unread = c.execute("""SELECT count(*) FROM messages WHERE channel_id=? AND contact_id=? AND direction='in' AND rowid>?
            AND coalesce(nullif(provider_timestamp,0),created_at)>=?""",
            (channel, contact, resolved["resolved_through"] if resolved else 0, resolved["external_reply_at"] if resolved else 0)).fetchone()[0]
        return {"unread": unread, "resolution": "open" if unread else resolved["reason"] if resolved else "none"}

    @staticmethod
    def inbound_marker(c, channel, contact, marker):
        marker = integer(marker, 0, 2**63-1)
        if marker and not c.execute("SELECT 1 FROM messages WHERE rowid=? AND channel_id=? AND contact_id=? AND direction='in'",
                                     (marker, channel, contact)).fetchone():
            raise ValueError("Marcador de mensagem invalido.")
        return marker

    @staticmethod
    def resolve_conversation(c, channel, contact, marker, reason, user):
        if not marker:
            return
        # Shared team checkpoint: a reply/close can only resolve its captured inbound boundary.
        c.execute("""INSERT INTO conversation_resolutions(channel_id,contact_id,resolved_through,reason,actor_id,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(channel_id,contact_id)
            DO UPDATE SET resolved_through=excluded.resolved_through,reason=excluded.reason,
                actor_id=excluded.actor_id,updated_at=excluded.updated_at
            WHERE excluded.resolved_through>resolved_through""", (channel, contact, marker, reason, str(user), time.time()))

    def close_conversation(self, p, user):
        marker = integer(p.get("cursor"), 1, 2**63-1)
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            self.conversation_contact(c, p.get("doctor_id"), p.get("channel_id"), p.get("contact_id"))
            self.inbound_marker(c, p["channel_id"], p["contact_id"], marker)
            self.resolve_conversation(c, p["channel_id"], p["contact_id"], marker, "closed", user)
            return self.conversation_status(c, p["channel_id"], p["contact_id"])

    def reply(self, p, user, enabled=False, *, sender_name):
        body = text(p.get("body"), 3000)
        name = sender_title(sender_name)
        request = text(p.get("request_id"), 100)
        doctor, channel, contact = p.get("doctor_id"), p.get("channel_id"), p.get("contact_id")
        source = p.get("source_channel_id") or channel
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            ch, ct = self.conversation_contact(c, doctor, channel, contact)
            self.conversation_contact(c, doctor, source, contact)
            previous = c.execute("SELECT m.*,r.user_id,r.source_channel_id FROM reply_requests r JOIN messages m ON m.id=r.message_id WHERE request_id=?", (request,)).fetchone()
            if previous:
                expected = signed_message(body, previous["sender_name"]) if previous["sender_name"] else body
                if (previous["channel_id"], previous["contact_id"], previous["body"], previous["user_id"], previous["source_channel_id"] or previous["channel_id"]) != (channel, contact, expected, str(user), source) or (p.get("phone") and phone(p["phone"]) != previous["recipient_phone"]):
                    raise Conflict("Identificador de envio ja utilizado.")
                return previous["id"]
            if not enabled or ch["state"] != "open":
                raise ValueError("Envios indisponiveis. Conecte o numero e habilite o servidor WhatsApp.")
            if ct["opted_out"]:
                raise ValueError("Contato descadastrado. Envio bloqueado.")
            recipient = self.conversation_peer(c, source, contact, ct["phone"])
            if p.get("phone") and phone(p["phone"]) != recipient:
                raise Conflict("O telefone desta conversa mudou. Atualize antes de enviar.")
            if c.execute("SELECT 1 FROM messages WHERE channel_id=? AND status='unknown' LIMIT 1", (channel,)).fetchone():
                raise ValueError("Ha envio sem confirmacao neste numero. Confira antes de continuar.")
            marker = self.inbound_marker(c, source, contact, p.get("cursor", 0))
            key = uuid.uuid4().hex
            c.execute("""INSERT INTO messages(id,channel_id,contact_id,body,direction,created_at,recipient_phone,sender_name,sender_user_id)
                VALUES(?,?,?,?,'out',?,?,?,?)""", (key, channel, contact, signed_message(body, name), time.time(), recipient, name, str(user)))
            c.execute("INSERT INTO reply_requests(request_id,message_id,user_id,source_channel_id,inbound_cursor) VALUES(?,?,?,?,?)",
                      (request, key, str(user), source, marker))
            return key

    def campaign(self, p, actor, actor_id=""):
        doctor, channel, now = p.get("doctor_id"), p.get("channel_id"), time.time()
        sender = sender_title(actor)
        ids = p.get("lead_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 5000 or any(not isinstance(i, str) for i in ids):
            raise ValueError("Selecione de 1 a 5000 oportunidades.")
        body = text(p.get("body"), 3000)
        if re.search(r"\{(?!nome\}|doutor\})", body):
            raise ValueError("Use somente {nome} e {doutor} na mensagem.")
        interval = integer(p.get("interval_seconds", 60), 30, 86400)
        start, end = integer(p.get("hour_from", 9), 8, 19), integer(p.get("hour_to", 18), 9, 20)
        minute_from, minute_to = integer(p.get("minute_from", 0), 0, 59), integer(p.get("minute_to", 0), 0, 59)
        if start*60+minute_from >= end*60+minute_to or end*60+minute_to > 20*60:
            raise ValueError("Horario final deve ser depois do inicial.")
        weekdays = p.get("weekdays", list(range(7)))
        if not isinstance(weekdays, list) or not weekdays or len(weekdays) != len(set(weekdays)) or any(type(d) is not int or d not in range(7) for d in weekdays):
            raise ValueError("Selecione os dias de funcionamento.")
        tag = text(p.get("tag", ""), 50, False)
        schedule = p.get("start_at", "")
        if schedule:
            parsed = datetime.fromisoformat(schedule)
            if parsed.tzinfo is not None:
                raise ValueError("Informe o agendamento no horario de Sao Paulo, sem fuso adicional.")
            start_at = parsed.replace(tzinfo=TZ).timestamp()
        else:
            start_at = now
        key = uuid.uuid4().hex
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            doc = self.row(c, "doctors", doctor)
            self.owned(c, "channels", channel, doctor)
            snapshot = None
            if p.get("_audience_id"):
                from crm_campaigns import fingerprint
                snapshot = self.row(c, "campaign_audiences", p["_audience_id"])
                if snapshot["doctor_id"] != doctor or snapshot["actor_id"] != str(actor_id):
                    raise LookupError()
                if snapshot["campaign_id"]:
                    return snapshot["campaign_id"]
                data = json.loads(snapshot["payload"])
                ids = data["ids"]
                if snapshot["expires_at"] < now or fingerprint(c, doctor, ids) != snapshot["digest"]:
                    raise Conflict("A selecao mudou ou expirou. Revise os destinatarios novamente.")
            recipients = {}
            for lead_id in set(ids):
                lead = self.owned(c, "leads", lead_id, doctor)
                contact = self.row(c, "contacts", lead["contact_id"])
                if contact["opted_out"] or contact["experts_status"] == "review":
                    raise ValueError("Contato descadastrado ou cadastro a conferir. Envio bloqueado.")
                recipients[contact["id"]] = contact
            c.execute("INSERT INTO campaigns(id,doctor_id,channel_id,name,body,interval_seconds,start_at,hour_from,hour_to,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (key, doctor, channel, text(p.get("name")), body, interval, start_at, start, end, now, actor))
            c.execute("UPDATE campaigns SET weekdays=?,minute_from=?,minute_to=?,tag=?,selected_count=?,duplicates_count=?,excluded_count=? WHERE id=?", (json.dumps(weekdays), minute_from, minute_to, tag,
                len(ids), len(ids)-len(recipients), 0, key))
            if snapshot:
                summary = data["summary"]
                c.execute("UPDATE campaigns SET selected_count=?,duplicates_count=?,excluded_count=? WHERE id=?", (summary["selected"], summary["duplicates"], summary["excluded"], key))
                c.execute("UPDATE campaign_audiences SET campaign_id=? WHERE id=?", (key, snapshot["id"]))
            for cid, contact in recipients.items():
                message = signed_message(body.replace("{nome}", contact["name"].split()[0]).replace("{doutor}", doc["name"]), sender) + FOOTER
                c.execute("INSERT INTO messages(id,channel_id,contact_id,campaign_id,body,direction,created_at,sender_name,sender_user_id,recipient_phone) VALUES(?,?,?,?,?,'out',?,?,?,?)",
                          (uuid.uuid4().hex, channel, cid, key, message, now, sender, str(actor_id), contact["phone"]))
        return key

    def campaign_status(self, key, doctor, action, enabled=False):
        target = {"start": "running", "pause": "paused", "cancel": "cancelled"}.get(action)
        if not target:
            raise ValueError("Acao invalida.")
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            campaign = self.owned(c, "campaigns", key, doctor)
            if campaign["status"] in {"done", "cancelled"}:
                raise ValueError("Campanha encerrada.")
            if action == "start":
                if not enabled:
                    raise ValueError("Envios desativados no piloto. Configure a conexao e habilite o worker primeiro.")
                channel = self.row(c, "channels", campaign["channel_id"])
                if channel["state"] != "open":
                    raise ValueError("Conecte o numero antes de iniciar.")
                if c.execute("SELECT 1 FROM messages WHERE campaign_id=? AND status IN ('sending','unknown')", (key,)).fetchone():
                    raise ValueError("Ha envio sem confirmacao. Confira no WhatsApp antes de continuar; ele nao sera reenviado automaticamente.")
            c.execute("UPDATE campaigns SET status=?,last_error='' WHERE id=?", (target, key))
            if action == "cancel":
                c.execute("UPDATE messages SET status='cancelled' WHERE campaign_id=? AND status='queued'", (key,))

    def heartbeat(self, now=None):
        with self.db() as c:
            c.execute("INSERT INTO runtime VALUES('worker',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (time.time() if now is None else now,))

    def worker_online(self):
        with self.db() as c:
            row = c.execute("SELECT value FROM runtime WHERE key='worker'").fetchone()
            return bool(row and 0 <= time.time() - row[0] < 75)

    def campaigns_allowed(self):
        with self.db() as c:
            row = c.execute("SELECT value FROM runtime WHERE key='campaign_dispatch_enabled'").fetchone()
        return bool(row and row[0] == 1)

    def enable_campaigns(self):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            # Enabling the feature cannot resume an old queue unattended.
            c.execute("UPDATE campaigns SET status='paused' WHERE status='running'")
            c.execute("INSERT INTO runtime VALUES('campaign_dispatch_enabled',1) ON CONFLICT(key) DO UPDATE SET value=1")

    def claim(self, now=None, *, allow_campaigns=True):
        now = time.time() if now is None else now
        local = datetime.fromtimestamp(now, TZ)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            # A worker crash after dispatch must never cause an automatic duplicate.
            stale = [r[0] for r in c.execute("SELECT DISTINCT campaign_id FROM messages WHERE status='sending' AND claimed_at<?", (now - 120,))]
            c.execute("UPDATE messages SET status='unknown' WHERE status='sending' AND claimed_at<?", (now - 120,))
            for key in stale:
                c.execute("UPDATE campaigns SET status='paused',last_error='Envio sem confirmacao. Revisao necessaria.' WHERE id=?", (key,))
            c.execute("UPDATE messages SET status='skipped',skip_reason='Descadastrado' WHERE status='queued' AND contact_id IN (SELECT id FROM contacts WHERE opted_out=1)")
            c.execute("""UPDATE messages SET status='skipped',skip_reason='Oportunidade indisponivel para este doutor'
                WHERE status='queued' AND campaign_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM leads l JOIN campaigns ca ON ca.doctor_id=l.doctor_id
                 WHERE ca.id=messages.campaign_id AND l.contact_id=messages.contact_id)""")
            c.execute("""UPDATE messages SET status='skipped',skip_reason='Cadastro ou telefone alterado; confira o contato'
                WHERE status='queued' AND campaign_id IS NOT NULL AND contact_id IN
                (SELECT id FROM contacts WHERE experts_status='review')""")
            c.execute("""UPDATE messages SET status='skipped',skip_reason='Telefone alterado; confira o contato'
                WHERE status='queued' AND campaign_id IS NOT NULL AND recipient_phone<>'' AND recipient_phone<>(SELECT phone FROM contacts WHERE id=messages.contact_id)""")
            c.execute("UPDATE campaigns SET status='done' WHERE status='running' AND NOT EXISTS(SELECT 1 FROM messages WHERE campaign_id=campaigns.id AND status IN ('queued','sending','unknown'))")
            rows = c.execute("""SELECT m.*,ch.instance,ch.daily_limit,ch.doctor_id,coalesce(nullif(m.recipient_phone,''),ct.phone) phone,coalesce(ca.interval_seconds,30) interval_seconds,ca.hour_from,ca.hour_to,ca.minute_from,ca.minute_to,ca.weekdays
                FROM messages m LEFT JOIN campaigns ca ON ca.id=m.campaign_id JOIN channels ch ON ch.id=m.channel_id
                JOIN contacts ct ON ct.id=m.contact_id WHERE m.status='queued' AND (m.campaign_id IS NULL OR (ca.status='running' AND ca.start_at<=?))
                AND ch.state='open' AND (m.campaign_id IS NULL OR ch.next_send_at<=?) AND ct.opted_out=0
                AND (? OR m.campaign_id IS NULL)
                AND NOT EXISTS(SELECT 1 FROM messages pending WHERE pending.channel_id=ch.id AND pending.status IN ('sending','unknown'))
                ORDER BY (m.campaign_id IS NOT NULL),m.created_at,m.id""", (now, now, bool(allow_campaigns))).fetchall()
            for row in rows:
                r = dict(row)
                if r["campaign_id"] and (local.weekday() not in json.loads(r["weekdays"]) or not r["hour_from"]*60+r["minute_from"] <= local.hour*60+local.minute < r["hour_to"]*60+r["minute_to"]):
                    continue
                if r["campaign_id"]:
                    used = c.execute("SELECT count(*) FROM messages WHERE channel_id=? AND campaign_id IS NOT NULL AND direction='out' AND (sent_at>=? OR claimed_at>=?)", (r["channel_id"], midnight, midnight)).fetchone()[0]
                    if used >= r["daily_limit"]:
                        continue
                c.execute("UPDATE messages SET status='sending',claimed_at=? WHERE id=?", (now, r["id"]))
                if r["campaign_id"]:
                    c.execute("UPDATE channels SET next_send_at=? WHERE id=?", (now + r["interval_seconds"], r["channel_id"]))
                return r
        return None

    def finish(self, item, provider_id=None, error=False):
        now = time.time()
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            if error or not provider_id:
                c.execute("UPDATE messages SET status='unknown' WHERE id=? AND status='sending'", (item["id"],))
                c.execute("UPDATE campaigns SET status='paused',last_error='Falha ou envio sem confirmacao. Confira o numero antes de continuar.' WHERE id=? AND status<>'cancelled'", (item["campaign_id"],))
            else:
                updated = c.execute("UPDATE messages SET status='sent',provider_id=?,sent_at=? WHERE id=? AND status='sending'", (provider_id, now, item["id"]))
                reply = c.execute("""SELECT r.*,m.contact_id FROM reply_requests r JOIN messages m ON m.id=r.message_id
                    WHERE r.message_id=? AND m.campaign_id IS NULL""", (item["id"],)).fetchone()
                if updated.rowcount and reply and reply["source_channel_id"]:
                    self.resolve_conversation(c, reply["source_channel_id"], reply["contact_id"], reply["inbound_cursor"], "replied", reply["user_id"])
                if updated.rowcount and item["campaign_id"]:
                    campaign = self.row(c, "campaigns", item["campaign_id"])
                    if campaign["tag"]:
                        c.execute("INSERT OR IGNORE INTO contact_tags VALUES(?,?,?,?)", (campaign["doctor_id"], item["contact_id"], campaign["tag"], now))

    def receive(self, instance, data, event, *, expected_contact=None, history=False):
        if not isinstance(data, dict) or not isinstance(event, str):
            return False
        event = event.lower().replace("_", ".")
        now = time.time()
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            ch = c.execute("SELECT * FROM channels WHERE instance=?", (instance,)).fetchone()
            if not ch:
                raise LookupError("Instancia desconhecida.")
            if event == "connection.update":
                state = data.get("state")
                if state not in {"open", "close", "connecting"}:
                    return
                c.execute("UPDATE channels SET state=? WHERE id=?", (state, ch["id"]))
                if state != "open":
                    c.execute("UPDATE campaigns SET status='paused',last_error='Numero desconectado.' WHERE channel_id=? AND status='running'", (ch["id"],))
                return
            if event not in {"messages.upsert", "send.message"}:
                return False
            key = data.get("key") or {}
            if not isinstance(key, dict) or (key.get("fromMe") is not None and type(key["fromMe"]) is not bool):
                return False
            jid = key.get("remoteJid", "")
            if isinstance(jid, str) and jid.endswith("@lid"):
                jid = key.get("remoteJidAlt", "")
            if not isinstance(jid, str) or not jid.endswith("@s.whatsapp.net"):
                return False
            number = phone(jid.split("@")[0].split(":")[0])
            from_me = key.get("fromMe") is True
            if (event == "send.message" or history) and not from_me:
                return False
            body = whatsapp_content(data.get("message"))
            provider_id = key.get("id")
            if not body or not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 200:
                return False
            stamp = whatsapp_time(data.get("messageTimestamp"), now)
            if history and not stamp:
                return False
            known = c.execute("SELECT ct.* FROM contacts ct JOIN contact_phones p ON p.contact_id=ct.id WHERE p.phone=?", (number,)).fetchone()
            if expected_contact and (not known or known["id"] != expected_contact):
                return False
            if c.execute("SELECT 1 FROM messages WHERE channel_id=? AND provider_id=?", (ch["id"], provider_id)).fetchone():
                return False
            if from_me:
                # An echo can arrive before the sendText HTTP response returns its ID.
                pending = c.execute("""SELECT id FROM messages WHERE channel_id=? AND direction='out' AND origin='crm'
                    AND status IN ('sending','unknown') AND provider_id IS NULL AND recipient_phone=? AND body=?
                    AND claimed_at>=? AND claimed_at<=?""", (ch["id"], number, body, (stamp or now)-120, (stamp or now)+120)).fetchall()
                if len(pending) == 1:
                    c.execute("UPDATE messages SET provider_id=?,provider_timestamp=? WHERE id=?", (provider_id, stamp, pending[0]["id"]))
                    return False
            name = (known["name"] if known else number) if from_me else str(data.get("pushName") or number)[:160]
            contact = self.contact(c, number, name)
            c.execute("""INSERT INTO messages(id,channel_id,contact_id,body,direction,status,provider_id,created_at,
                recipient_phone,sent_at,sender_name,origin,provider_timestamp) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, ch["id"], contact["id"], body, "out" if from_me else "in", "sent" if from_me else "received",
                 provider_id, stamp or now, number, (stamp or now) if from_me else None,
                 "WhatsApp / sistema externo" if from_me else "", "external" if from_me else "whatsapp", stamp))
            normalized = unicodedata.normalize("NFKD", body).encode("ascii", "ignore").decode().strip().upper()
            if not from_me and normalized in {"SAIR", "PARAR", "STOP", "CANCELAR", "NAO QUERO RECEBER MAIS MENSAGENS"}:
                c.execute("UPDATE contacts SET opted_out=1 WHERE id=?", (contact["id"],))
                c.execute("UPDATE messages SET status='skipped',skip_reason='Descadastrado' WHERE contact_id=? AND status='queued'", (contact["id"],))
            if from_me and stamp:
                c.execute("""INSERT INTO conversation_resolutions(channel_id,contact_id,resolved_through,reason,actor_id,updated_at,external_reply_at)
                    VALUES(?,?,0,'replied','external',?,?) ON CONFLICT(channel_id,contact_id) DO UPDATE SET
                    reason=CASE WHEN excluded.external_reply_at>=conversation_resolutions.updated_at THEN 'replied' ELSE conversation_resolutions.reason END,
                    actor_id=CASE WHEN excluded.external_reply_at>=conversation_resolutions.updated_at THEN 'external' ELSE conversation_resolutions.actor_id END,
                    updated_at=max(conversation_resolutions.updated_at,excluded.updated_at),
                    external_reply_at=max(conversation_resolutions.external_reply_at,excluded.external_reply_at)""", (ch["id"], contact["id"], stamp, stamp))
                if not history:
                    incoming = c.execute("""SELECT max(rowid),max(coalesce(nullif(provider_timestamp,0),created_at)) FROM messages
                        WHERE channel_id=? AND contact_id=? AND direction='in'""", (ch["id"], contact["id"])).fetchone()
                    if incoming[0] and incoming[1] <= stamp:
                        self.resolve_conversation(c, ch["id"], contact["id"], incoming[0], "replied", "external")
            existing = c.execute("SELECT id FROM leads WHERE doctor_id=? AND contact_id=?", (ch["doctor_id"], contact["id"])).fetchone()
            first = c.execute("SELECT s.id,s.pipeline_id FROM stages s JOIN pipelines p ON p.id=s.pipeline_id WHERE p.doctor_id=? AND s.kind='open' ORDER BY p.name,s.position LIMIT 1", (ch["doctor_id"],)).fetchone()
            if not existing and first:
                c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (uuid.uuid4().hex, ch["doctor_id"], first["pipeline_id"], first["id"], contact["id"], "Conversa no WhatsApp" if from_me else "Conversa recebida", now, now))
            return True
