"""Outbound-only Vielle pilot. Immutable call identity; private recording archive."""
from dataclasses import dataclass
from datetime import datetime
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from crm_store import Conflict, TZ, integer, text

SID = re.compile(r"^(AC|AP|SK|CA|RE|PN)[a-fA-F0-9]{32}$")
TERMINAL = {"completed", "busy", "no-answer", "failed", "canceled", "declined", "unknown"}
NOTICE = "Olá. Aqui é a equipe da Vielle Clinic. Esta ligação será gravada para acompanhamento do seu atendimento. Para concordar e falar com a equipe, pressione 1. Caso não concorde, desligue."
MAX_AUDIO = 32 * 1024 * 1024


def sid(value, prefix):
    return isinstance(value, str) and value.startswith(prefix) and bool(SID.fullmatch(value))


def brazil_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"\+55[1-9]\d{9,10}", value):
        raise ValueError("Informe um telefone brasileiro completo, com +55 e DDD.")
    return value


@dataclass
class VoiceConfig:
    account: str = ""
    auth_token: str = ""
    api_key: str = ""
    api_secret: str = ""
    app_sid: str = ""
    origin: str = ""
    lines: tuple = ()
    enabled: bool = False
    max_seconds: int = 1200
    daily_limit: int = 100
    notice_mode: str = "staff"

    @classmethod
    def from_env(cls):
        try:
            lines = json.loads(os.getenv("CRM_VOICE_LINES_JSON", "[]"))
            if not isinstance(lines, list) or len(lines) > 20:
                raise ValueError()
            clean = []
            for line in lines:
                clean.append({"id": text(line["id"], 64), "doctor_id": text(line["doctor_id"], 64),
                              "name": text(line["name"], 80), "number": brazil_number(line["number"])})
            if len({x["id"] for x in clean}) != len(clean):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            clean = []
        notice_mode = os.getenv("CRM_VOICE_NOTICE_MODE", "staff")
        if notice_mode not in {"staff", "automated"}:
            notice_mode = "automated"
        return cls(os.getenv("TWILIO_ACCOUNT_SID", ""), os.getenv("TWILIO_AUTH_TOKEN", ""),
                   os.getenv("TWILIO_API_KEY_SID", ""), os.getenv("TWILIO_API_KEY_SECRET", ""),
                   os.getenv("TWILIO_TWIML_APP_SID", ""), os.getenv("CRM_VOICE_PUBLIC_URL", "").rstrip("/"),
                   tuple(clean), os.getenv("CRM_VOICE_ENABLED") == "1",
                   integer(os.getenv("CRM_VOICE_MAX_SECONDS", "1200"), 60, 3600),
                   integer(os.getenv("CRM_VOICE_DAILY_LIMIT", "100"), 1, 1000), notice_mode)

    @property
    def credentials_ready(self):
        return (sid(self.account, "AC") and len(self.auth_token) >= 24 and sid(self.api_key, "SK")
                and len(self.api_secret) >= 24 and sid(self.app_sid, "AP"))

    @property
    def origin_ready(self):
        parsed = urlsplit(self.origin)
        return parsed.scheme == "https" and bool(parsed.netloc) and not (parsed.path or parsed.query or parsed.fragment or parsed.username)

    def url(self, action, key):
        if not self.origin_ready:
            raise ValueError("Endereco publico da telefonia nao configurado.")
        return f"{self.origin}/api/crm-voice-webhook/{action}?id={key}"

    def status(self, doctor):
        lines = [x for x in self.lines if x["doctor_id"] == doctor]
        ready = bool(self.credentials_ready and self.origin_ready and lines)
        return {"ready": ready, "enabled": bool(ready and self.enabled), "lines": lines,
                "max_seconds": self.max_seconds, "daily_limit": self.daily_limit, "notice_mode": self.notice_mode,
                "checks": {"credentials": bool(self.credentials_ready), "public_url": self.origin_ready,
                           "number": bool(lines), "activation": self.enabled}}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class TwilioVoice:
    def __init__(self, config):
        self.config = config

    def client(self):
        from twilio.http.http_client import TwilioHttpClient
        from twilio.rest import Client
        return Client(self.config.api_key, self.config.api_secret, self.config.account,
                      http_client=TwilioHttpClient(timeout=12, max_retries=0))

    def token(self, key):
        from twilio.jwt.access_token import AccessToken
        from twilio.jwt.access_token.grants import VoiceGrant
        token = AccessToken(self.config.account, self.config.api_key, self.config.api_secret,
                            identity="crm-call-" + key, ttl=120)
        token.add_grant(VoiceGrant(outgoing_application_sid=self.config.app_sid, incoming_allow=False))
        return token.to_jwt()

    def owns_number(self, number):
        result = self.client().incoming_phone_numbers.list(phone_number=number, limit=2)
        return any(n.phone_number == number and n.capabilities.get("voice") for n in result)

    def record(self, parent, key):
        return self.client().calls(parent).recordings.create(recording_channels="dual", recording_track="both",
            recording_status_callback=self.config.url("recording", key),
            recording_status_callback_method="POST", recording_status_callback_event=["completed", "absent"]).sid

    def hangup(self, parent):
        self.client().calls(parent).update(status="completed")

    def recording_state(self, recording):
        item = self.client().recordings(recording).fetch()
        return {"status": item.status, "duration": item.duration, "call_sid": item.call_sid}

    def audio(self, recording):
        # Never fetch a callback-supplied URL, redirect or arbitrary account path.
        if not sid(recording, "RE"):
            raise ValueError("Gravacao invalida.")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.config.account}/Recordings/{recording}.mp3"
        credential = base64.b64encode(f"{self.config.account}:{self.config.auth_token}".encode()).decode()
        with build_opener(NoRedirect).open(Request(url, headers={"Authorization": "Basic " + credential}), timeout=25) as response:
            if response.status != 200 or response.headers.get_content_type() not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                raise ValueError("Audio indisponivel.")
            body = response.read(MAX_AUDIO + 1)
            if not body or len(body) > MAX_AUDIO:
                raise ValueError("Tamanho do audio invalido.")
            return body


class VoiceService:
    def __init__(self, store, config=None, gateway=None):
        self.store = store
        self.config = config or VoiceConfig.from_env()
        self.gateway = gateway or TwilioVoice(self.config)
        self.root = store.path.parent / "voice-recordings"
        self.root.mkdir(mode=0o700, exist_ok=True)
        with store.db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS voice_calls(
                id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,lead_id TEXT NOT NULL REFERENCES leads(id),
                contact_id TEXT NOT NULL REFERENCES contacts(id),doctor_id TEXT NOT NULL REFERENCES doctors(id),
                patient_uuid TEXT NOT NULL,customer_name TEXT NOT NULL,destination TEXT NOT NULL,
                line_id TEXT NOT NULL,caller_number TEXT NOT NULL,line_name TEXT NOT NULL,
                actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,account_sid TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared',parent_sid TEXT NOT NULL DEFAULT '',child_sid TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,updated_at REAL NOT NULL,answered_at REAL,ended_at REAL,duration INTEGER NOT NULL DEFAULT 0,
                consent_at REAL,notice TEXT NOT NULL,recording_sid TEXT NOT NULL DEFAULT '',
                recording_status TEXT NOT NULL DEFAULT 'waiting',recording_duration INTEGER NOT NULL DEFAULT 0,
                audio_hash TEXT NOT NULL DEFAULT '',audio_bytes INTEGER NOT NULL DEFAULT 0,
                download_attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,
                outcome TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',max_seconds INTEGER NOT NULL,
                notice_mode TEXT NOT NULL DEFAULT 'automated',consent_source TEXT NOT NULL DEFAULT 'patient_keypad');
            CREATE INDEX IF NOT EXISTS voice_customer ON voice_calls(doctor_id,contact_id,created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS voice_parent ON voice_calls(parent_sid) WHERE parent_sid<>'';
            CREATE UNIQUE INDEX IF NOT EXISTS voice_recording ON voice_calls(recording_sid) WHERE recording_sid<>'';
            """)
            c.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in c.execute("PRAGMA table_info(voice_calls)")}
            for name, default in (("notice_mode", "automated"), ("consent_source", "patient_keypad")):
                if name not in columns:
                    c.execute(f"ALTER TABLE voice_calls ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")

    def row(self, c, key, doctor=None):
        call = self.store.row(c, "voice_calls", key)
        if doctor is not None and call["doctor_id"] != doctor:
            raise LookupError()
        return call

    @staticmethod
    def public(call):
        result = {k: call[k] for k in ("id", "lead_id", "contact_id", "doctor_id", "customer_name", "destination",
            "line_name", "caller_number", "actor_name", "status", "created_at", "answered_at", "ended_at", "duration",
            "consent_at", "consent_source", "notice_mode", "recording_status", "recording_duration", "outcome", "note")}
        result["can_retry_recording"] = bool(call["recording_sid"] and call["recording_status"] == "error")
        return result

    @staticmethod
    def expire(c, now):
        c.execute("UPDATE voice_calls SET status='canceled',ended_at=?,updated_at=? WHERE status='prepared' AND created_at<?", (now, now, now-120))
        c.execute("UPDATE voice_calls SET status='unknown',ended_at=?,updated_at=? WHERE status NOT IN ('prepared','completed','busy','no-answer','failed','canceled','declined','unknown') AND created_at+max_seconds+300<?", (now, now, now))

    def history(self, lead_id, doctor, page=1):
        page = integer(page, 1, 10000)
        with self.store.db() as c:
            lead = self.store.owned(c, "leads", lead_id, doctor)
            now = time.time()
            self.expire(c, now)
            params = (doctor, lead["contact_id"])
            from crm_access import pipeline_visible
            visible = [r for r in c.execute("""SELECT v.*,l.pipeline_id,p.institute_key FROM voice_calls v
                JOIN leads l ON l.id=v.lead_id JOIN pipelines p ON p.id=l.pipeline_id
                WHERE v.doctor_id=? AND v.contact_id=? ORDER BY v.created_at DESC,v.id""", params) if pipeline_visible(dict(r))]
            total, rows = len(visible), visible[(page-1)*20:page*20]
        return {"calls": [self.public(dict(r)) for r in rows], "total": total, "page": page,
                "has_more": page*20 < total, "config": self.config.status(doctor)}

    def prepare(self, payload, actor):
        doctor = payload.get("doctor_id")
        if not self.config.status(doctor)["enabled"]:
            raise ValueError("Telefonia ainda nao ativada. Aguarde o numero e a configuracao da Twilio.")
        request_id = text(payload.get("request_id"), 64)
        if not re.fullmatch(r"[a-f0-9-]{32,36}", request_id):
            raise ValueError("Identificador de ligacao invalido.")
        line = next((x for x in self.config.lines if x["doctor_id"] == doctor and x["id"] == payload.get("line_id")), None)
        if not line:
            raise ValueError("Numero indisponivel para este doutor.")
        # Provider ownership is checked before issuing any browser dial capability.
        if not self.gateway.owns_number(line["number"]):
            raise ValueError("O numero de origem nao e um numero de voz desta conta Twilio.")
        now = time.time()
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            self.expire(c, now)
            lead = self.store.owned(c, "leads", payload.get("lead_id"), doctor)
            contact = self.store.row(c, "contacts", lead["contact_id"])
            if not contact["phone"]:
                raise ValueError("Telefone removido da ficha. Cadastre um telefone antes de ligar.")
            if contact["opted_out"]:
                raise ValueError("Contato descadastrado. As ligacoes para este contato estao bloqueadas.")
            if contact["experts_status"] == "review":
                raise ValueError("Confira o vinculo do paciente antes de ligar.")
            destination = brazil_number("+" + contact["phone"])
            existing = c.execute("SELECT * FROM voice_calls WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                old = dict(existing)
                if old["lead_id"] != lead["id"] or old["actor_id"] != str(actor["id"]) or old["line_id"] != line["id"]:
                    raise Conflict("Identificador de outra ligacao.")
                if old["status"] != "prepared" or now-old["created_at"] >= 120:
                    raise Conflict("Esta tentativa ja foi utilizada. Confira o historico antes de ligar novamente.")
                return {"call": self.public(old), "token": self.gateway.token(old["id"])}
            active = c.execute("SELECT actor_id FROM voice_calls WHERE status NOT IN ('completed','busy','no-answer','failed','canceled','declined','unknown')").fetchall()
            if any(r["actor_id"] == str(actor["id"]) for r in active) or len(active) >= 5:
                raise Conflict("Ja existe uma ligacao em andamento. Confira ou encerre antes de iniciar outra.")
            midnight = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            if c.execute("SELECT count(*) FROM voice_calls WHERE created_at>=?", (midnight,)).fetchone()[0] >= self.config.daily_limit:
                raise ValueError("Limite diario de telefonia atingido. Consulte o Master.")
            key = uuid.uuid4().hex
            c.execute("""INSERT INTO voice_calls(id,request_id,lead_id,contact_id,doctor_id,patient_uuid,customer_name,
                destination,line_id,caller_number,line_name,actor_id,actor_name,account_sid,created_at,updated_at,notice,max_seconds,notice_mode,consent_source)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (key, request_id, lead["id"], contact["id"], doctor,
                contact["experts_uuid"], contact["name"], destination, line["id"], line["number"], line["name"],
                str(actor["id"]), actor["nome"], self.config.account, now, now,
                NOTICE if self.config.notice_mode == "automated" else "", self.config.max_seconds, self.config.notice_mode, ""))
            c.execute("INSERT INTO events(lead_id,actor,kind,body,created_at) VALUES(?,?,?,?,?)", (lead["id"], actor["nome"], "call", "Ligacao preparada para " + destination, now))
            call = self.row(c, key)
        return {"call": self.public(call), "token": self.gateway.token(key)}

    def twiml(self, key, params):
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            call = self.row(c, key)
            contact = self.store.row(c, "contacts", call["contact_id"])
            if (not self.config.status(call["doctor_id"])["enabled"] or call["status"] != "prepared"
                or params.get("AccountSid") != call["account_sid"]
                or time.time()-call["created_at"] >= 120 or params.get("From") != "client:crm-call-" + key
                or not sid(params.get("CallSid"), "CA") or contact["opted_out"] or contact["experts_status"] == "review"
                or contact["phone"] != call["destination"][1:]):
                response.hangup()
                return str(response)
            c.execute("UPDATE voice_calls SET parent_sid=?,status='initiated',updated_at=? WHERE id=?", (params["CallSid"], time.time(), key))
        dial = response.dial(caller_id=call["caller_number"], answer_on_bridge=True, timeout=30,
                             time_limit=call["max_seconds"], action=self.config.url("finished", key), method="POST")
        notice = {"url": self.config.url("notice", key), "method": "POST"} if call["notice_mode"] == "automated" else {}
        dial.number(call["destination"], **notice,
                    status_callback=self.config.url("status", key), status_callback_method="POST",
                    status_callback_event="initiated ringing answered completed")
        return str(response)

    def child(self, c, key, params):
        call = self.row(c, key)
        child = params.get("CallSid", "")
        if not sid(child, "CA") or params.get("ParentCallSid") != call["parent_sid"] or not call["parent_sid"]:
            raise ValueError("Ligacao nao reconhecida.")
        if call["child_sid"] and call["child_sid"] != child:
            raise ValueError("Ligacao nao reconhecida.")
        c.execute("UPDATE voice_calls SET child_sid=? WHERE id=?", (child, key))
        return call

    def callback(self, action, key, params):
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        now = time.time()
        if action == "twiml":
            return self.twiml(key, params)
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            call = self.row(c, key)
            if params.get("AccountSid") != call["account_sid"]:
                raise ValueError("Conta nao reconhecida.")
            if action in {"notice", "consent"} and call["notice_mode"] != "automated":
                raise ValueError("Esta ligacao exige confirmacao verbal pelo atendente.")
            if action in {"notice", "consent", "status"}:
                call = self.child(c, key, params)
            elif action in {"recording", "finished"} and params.get("CallSid") != call["parent_sid"]:
                raise ValueError("Ligacao nao reconhecida.")
            if action == "notice":
                if call["status"] in TERMINAL:
                    response.hangup()
                else:
                    response.gather(num_digits=1, timeout=12, action=self.config.url("consent", key), method="POST").say(call["notice"], language="pt-BR", voice="alice")
                    response.hangup()
                return str(response)
            if action == "consent":
                if call["consent_at"] and call["status"] not in TERMINAL:
                    return str(response)
                if call["status"] in TERMINAL or params.get("Digits") != "1":
                    if not call["consent_at"] and call["status"] not in TERMINAL:
                        c.execute("UPDATE voice_calls SET status='declined',ended_at=?,updated_at=? WHERE id=?", (now, now, key))
                    response.hangup()
                    return str(response)
                # Claim once before the external recording request. Unknown failures never re-record automatically.
                c.execute("UPDATE voice_calls SET consent_at=?,consent_source='patient_keypad',recording_status='starting',status='in-progress',answered_at=?,updated_at=? WHERE id=?", (now, now, now, key))
            elif action == "status":
                status = params.get("CallStatus")
                if status not in {"initiated", "ringing", "in-progress", "completed", "busy", "no-answer", "failed", "canceled"}:
                    raise ValueError("Status invalido.")
                order = {"prepared": 0, "initiated": 1, "ringing": 2, "in-progress": 3}
                if call["status"] not in TERMINAL and (status in TERMINAL or order.get(status, 0) > order.get(call["status"], 0)):
                    c.execute("UPDATE voice_calls SET status=?,updated_at=?,ended_at=coalesce(?,ended_at),duration=max(duration,?) WHERE id=?", (status, now, now if status in TERMINAL else None,
                        integer(params.get("CallDuration", "0"), 0, 86400), key))
                    if status == "in-progress" and call["notice_mode"] == "staff":
                        c.execute("UPDATE voice_calls SET answered_at=coalesce(answered_at,?) WHERE id=?", (now, key))
                return str(response)
            elif action == "finished":
                if call["status"] not in {"declined", "canceled", "failed", "busy", "no-answer"}:
                    status = params.get("DialCallStatus", "failed")
                    if status not in TERMINAL:
                        raise ValueError("Status invalido.")
                    c.execute("UPDATE voice_calls SET status=?,ended_at=?,updated_at=?,duration=max(duration,?) WHERE id=?", (status, now, now, integer(params.get("DialCallDuration", "0"), 0, 86400), key))
                response.hangup()
                return str(response)
            elif action == "recording":
                recording = params.get("RecordingSid", "")
                status = params.get("RecordingStatus")
                if not call["consent_at"] or not sid(recording, "RE") or status not in {"completed", "absent", "failed"}:
                    raise ValueError("Gravacao nao reconhecida.")
                if call["recording_sid"] and call["recording_sid"] != recording:
                    raise ValueError("Gravacao nao reconhecida.")
                if call["recording_status"] != "stored":
                    c.execute("UPDATE voice_calls SET recording_sid=?,recording_status=?,recording_duration=?,updated_at=? WHERE id=?", (recording, "queued" if status == "completed" else "absent",
                        integer(params.get("RecordingDuration", "0"), 0, 86400), now, key))
                return str(response)
            else:
                raise LookupError()
        try:
            recording = self.gateway.record(call["parent_sid"], key)
            if not sid(recording, "RE"):
                raise ValueError()
            with self.store.db() as c:
                c.execute("UPDATE voice_calls SET recording_sid=?,recording_status=CASE WHEN recording_status='starting' THEN 'recording' ELSE recording_status END WHERE id=?", (recording, key))
            return str(response)
        except Exception:
            with self.store.db() as c:
                c.execute("UPDATE voice_calls SET recording_status='error',status='failed',ended_at=?,updated_at=? WHERE id=?", (now, now, key))
            response.say("Não foi possível iniciar a gravação. A ligação será encerrada.", language="pt-BR", voice="alice")
            response.hangup()
            return str(response)

    def start_recording(self, payload, actor):
        if payload.get("verbal_authorization") is not True:
            raise ValueError("Confirme que avisou o paciente e que ele concordou com a gravacao.")
        now = time.time()
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            call = self.row(c, payload.get("id"), payload.get("doctor_id"))
            if call["actor_id"] != str(actor["id"]):
                raise ValueError("Somente o atendente desta ligacao pode iniciar a gravacao.")
            if (call["notice_mode"] != "staff" or not self.config.status(call["doctor_id"])["enabled"]
                or call["account_sid"] != self.config.account or call["status"] != "in-progress"
                or not sid(call["parent_sid"], "CA")):
                raise ValueError("Aguarde a conversa conectar antes de iniciar a gravacao.")
            contact = self.store.row(c, "contacts", call["contact_id"])
            if contact["opted_out"] or contact["experts_status"] == "review" or "+" + contact["phone"] != call["destination"]:
                raise ValueError("Confira o cadastro do contato antes de gravar.")
            if call["consent_at"] or call["recording_status"] != "waiting":
                return {"call": self.public(call)}
            # One deliberate, audited attestation per call; timeouts never start a second recording.
            c.execute("UPDATE voice_calls SET consent_at=?,consent_source='staff_verbal',recording_status='starting',updated_at=? WHERE id=?", (now, now, call["id"]))
            c.execute("INSERT INTO events(lead_id,actor,kind,body,created_at) VALUES(?,?,?,?,?)", (call["lead_id"], actor["nome"],
                "call_recording_authorization", json.dumps({"call_id": call["id"], "source": "staff_verbal",
                "statement": "Atendente confirmou que avisou o paciente e obteve concordancia verbal para iniciar a gravacao."}, ensure_ascii=False), now))
        try:
            recording = self.gateway.record(call["parent_sid"], call["id"])
            if not sid(recording, "RE"):
                raise ValueError()
            with self.store.db() as c:
                c.execute("UPDATE voice_calls SET recording_sid=?,recording_status=CASE WHEN recording_status='starting' THEN 'recording' ELSE recording_status END WHERE id=?", (recording, call["id"]))
        except Exception:
            with self.store.db() as c:
                c.execute("UPDATE voice_calls SET recording_status=CASE WHEN recording_status='starting' THEN 'error' ELSE recording_status END,updated_at=? WHERE id=?", (time.time(), call["id"]))
        with self.store.db() as c:
            return {"call": self.public(self.row(c, call["id"], call["doctor_id"]))}

    def save_note(self, payload, actor):
        note = text(payload.get("note", ""), 2000, required=False)
        outcome = payload.get("outcome", "")
        if outcome not in {"", "return", "scheduled", "proposal", "resolved", "followup"}:
            raise ValueError("Resultado invalido.")
        with self.store.db() as c:
            call = self.row(c, payload.get("id"), payload.get("doctor_id"))
            c.execute("UPDATE voice_calls SET note=?,outcome=? WHERE id=?", (note, outcome, call["id"]))
            c.execute("INSERT INTO events(lead_id,actor,kind,body,created_at) VALUES(?,?,?,?,?)", (call["lead_id"], actor["nome"], "call_note", json.dumps({"call_id": call["id"], "outcome": outcome, "note": note}, ensure_ascii=False), time.time()))

    def cancel(self, payload, actor):
        with self.store.db() as c:
            call = self.row(c, payload.get("id"), payload.get("doctor_id"))
            if call["actor_id"] != str(actor["id"]):
                raise ValueError("Esta ligacao pertence a outro atendente.")
            if call["status"] in TERMINAL:
                return
            if call["status"] == "prepared":
                c.execute("UPDATE voice_calls SET status='canceled',ended_at=?,updated_at=? WHERE id=?", (time.time(), time.time(), call["id"]))
                return
        self.gateway.hangup(call["parent_sid"])
        with self.store.db() as c:
            c.execute("UPDATE voice_calls SET status='canceled',ended_at=?,updated_at=? WHERE id=? AND status NOT IN ('completed','declined')", (time.time(), time.time(), call["id"]))

    def archive_one(self):
        if not self.config.credentials_ready:
            return False
        now = time.time()
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE voice_calls SET recording_status='queued' WHERE recording_status='downloading' AND retry_at<?", (now,))
            r = c.execute("SELECT * FROM voice_calls WHERE recording_status='queued' AND retry_at<=? AND download_attempts<5 ORDER BY created_at LIMIT 1", (now,)).fetchone()
            if not r:
                return False
            call = dict(r)
            c.execute("UPDATE voice_calls SET recording_status='downloading',download_attempts=download_attempts+1,retry_at=? WHERE id=?", (now+180, call["id"]))
        temp = self.root / (call["id"] + ".tmp")
        try:
            if call["account_sid"] != self.config.account:
                raise ValueError()
            audio = self.gateway.audio(call["recording_sid"])
            if not audio or len(audio) > MAX_AUDIO:
                raise ValueError()
            with temp.open("wb") as output:
                os.chmod(temp, 0o600)
                output.write(audio)
                output.flush()
                os.fsync(output.fileno())
            temp.replace(self.root / (call["id"] + ".mp3"))
            with self.store.db() as c:
                c.execute("UPDATE voice_calls SET recording_status='stored',audio_hash=?,audio_bytes=? WHERE id=?", (hashlib.sha256(audio).hexdigest(), len(audio), call["id"]))
        except Exception:
            temp.unlink(missing_ok=True)
            with self.store.db() as c:
                c.execute("UPDATE voice_calls SET recording_status=?,retry_at=? WHERE id=?", ("error" if call["download_attempts"] >= 4 else "queued", now+min(900, 30*2**call["download_attempts"]), call["id"]))
        return True

    def recover_one(self):
        if not self.config.credentials_ready:
            return
        now = time.time()
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT * FROM voice_calls WHERE recording_sid<>'' AND recording_status IN ('recording','starting') AND created_at+max_seconds+180<? AND retry_at<? LIMIT 1", (now, now)).fetchone()
            if not r:
                return
            call = dict(r)
            c.execute("UPDATE voice_calls SET retry_at=? WHERE id=?", (now+300, call["id"]))
        try:
            if call["account_sid"] != self.config.account:
                return
            data = self.gateway.recording_state(call["recording_sid"])
            if data["call_sid"] != call["parent_sid"] or data["status"] not in {"completed", "absent", "deleted"}:
                return
            with self.store.db() as c:
                c.execute("UPDATE voice_calls SET recording_status=?,recording_duration=?,retry_at=0 WHERE id=? AND recording_status IN ('recording','starting')", ("queued" if data["status"] == "completed" else "absent", integer(str(data["duration"] or 0), 0, 86400), call["id"]))
        except Exception:
            pass

    def retry_recording(self, payload):
        with self.store.db() as c:
            call = self.row(c, payload.get("id"), payload.get("doctor_id"))
            if not call["recording_sid"] or call["recording_status"] != "error":
                raise ValueError("Esta gravacao nao pode ser buscada novamente.")
            c.execute("UPDATE voice_calls SET recording_status='queued',download_attempts=0,retry_at=0 WHERE id=?", (call["id"],))

    def audio_path(self, key, doctor):
        with self.store.db() as c:
            call = self.row(c, key, doctor)
        if call["recording_status"] != "stored":
            raise LookupError()
        path = self.root / (call["id"] + ".mp3")
        if not path.is_file() or path.stat().st_size != call["audio_bytes"]:
            raise LookupError()
        return path


def run_archive(auth, stop):
    """Independent of the WhatsApp send worker; provider delays cannot block replies."""
    try:
        from crm_api import store_for
        voice = VoiceService(store_for(auth))
    except Exception:
        return
    while not stop.wait(5):
        try:
            voice.archive_one()
            voice.recover_one()
        except Exception:
            continue
