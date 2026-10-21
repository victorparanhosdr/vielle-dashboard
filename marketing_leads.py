"""Public demonstration requests, isolated from clinical CRM contacts."""
from collections import deque
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs

import phonenumbers
from auth_store import utc_now

STAGES = {"new": "Novo", "contacted": "Em contato", "demo": "Demonstração", "proposal": "Proposta", "won": "Contratado", "closed": "Encerrado"}
PLANS = ("Demonstração", "Essencial", "Conectado", "Institutos")
ATTRIBUTION_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
_init_lock = threading.Lock()


def text_field(value, label, limit, *, required=False):
    if not isinstance(value, str):
        raise ValueError(f"Confira o campo {label}.")
    value = value.strip()
    if len(value) > limit or required and not value or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError(f"Confira o campo {label} (até {limit} caracteres).")
    return value


def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("Envie um pedido válido.")
    allowed = {"request_id", "name", "email", "phone", "organization", "plan", "message", "website", "privacy", "attribution", "source_path"}
    if set(data) - allowed or data.get("privacy") is not True or data.get("website", ""):
        raise ValueError("Confira os dados e a ciência da política de privacidade.")
    request_id = data.get("request_id", "")
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("Atualize a página e tente novamente.")
    fields = {key: text_field(data.get(key, ""), label, limit, required=required)
              for key, label, limit, required in (("name", "nome", 100, True), ("organization", "organização", 140, True),
                  ("email", "e-mail", 180, True), ("phone", "telefone", 32, True), ("message", "mensagem", 1000, False))}
    fields["email"] = fields["email"].casefold()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", fields["email"]):
        raise ValueError("Informe um e-mail válido.")
    try:
        number = phonenumbers.parse(fields["phone"], "BR")
        if not phonenumbers.is_valid_number(number) or number.extension:
            raise ValueError()
        fields["phone"] = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except (phonenumbers.NumberParseException, ValueError):
        raise ValueError("Confira o país, o DDD e o telefone informado.") from None
    if data.get("plan") not in PLANS:
        raise ValueError("Selecione uma opção de interesse.")
    attribution = data.get("attribution", {})
    if not isinstance(attribution, dict) or set(attribution) - set(ATTRIBUTION_KEYS):
        raise ValueError("Origem do pedido inválida.")
    fields.update(plan=data["plan"], request_id=request_id,
                  attribution={key: text_field(attribution.get(key, ""), key, 200) for key in ATTRIBUTION_KEYS},
                  source_path=text_field(data.get("source_path", "/inicio"), "página de origem", 60))
    if fields["source_path"] not in {"/", "/inicio", "/inicio/", "/home.html", "/planos", "/planos/", "/plans.html"}:
        raise ValueError("Página de origem inválida.")
    return fields


class RequestLimiter:
    def __init__(self, limit=8):
        self.lock = threading.Lock()
        self.requests = {}
        self.limit = limit

    def allow(self, peer):
        now = time.monotonic()
        with self.lock:
            self.requests = {key: stamps for key, stamps in self.requests.items() if stamps[-1] > now - 600}
            key = hashlib.sha256(str(peer).encode()).hexdigest()
            stamps = self.requests.get(key, deque())
            while stamps and stamps[0] <= now - 600:
                stamps.popleft()
            if len(stamps) >= self.limit or key not in self.requests and len(self.requests) >= 2000:
                return False
            stamps.append(now)
            self.requests[key] = stamps
            return True


class MarketingLeadStore:
    def __init__(self, auth):
        self.auth = auth

    def initialize(self):
        with self.auth.connection() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS marketing_leads (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
                    name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL, organization TEXT NOT NULL,
                    plan TEXT NOT NULL, message TEXT NOT NULL, attribution_json TEXT NOT NULL,
                    source_path TEXT NOT NULL, privacy_version TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new', notes TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS marketing_leads_created ON marketing_leads(created_at DESC);
                CREATE INDEX IF NOT EXISTS marketing_leads_status ON marketing_leads(status,created_at DESC);
            """)

    @staticmethod
    def record(row):
        result = dict(row)
        result["attribution"] = json.loads(result.pop("attribution_json"))
        result.pop("request_id", None)
        result.pop("request_hash", None)
        return result

    def submit(self, data):
        values = validate_request(data)
        digest = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        now = utc_now()
        with self.auth.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute("SELECT id,request_hash FROM marketing_leads WHERE request_id=?", (values["request_id"],)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise ValueError("O pedido mudou. Atualize a página antes de tentar novamente.")
                return previous["id"]
            lead_id = secrets.token_hex(16)
            c.execute("""INSERT INTO marketing_leads
                (id,request_id,request_hash,name,email,phone,organization,plan,message,attribution_json,
                 source_path,privacy_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lead_id, values["request_id"], digest, values["name"], values["email"], values["phone"],
                 values["organization"], values["plan"], values["message"], json.dumps(values["attribution"], ensure_ascii=False),
                 values["source_path"], "2026-10-21", now, now))
        return lead_id

    def listing(self, search="", status="", page=1):
        search = text_field(search, "busca", 160)
        if status not in ("", *STAGES) or not 1 <= page <= 1000000:
            raise ValueError("Filtro inválido.")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        where = "(?='' OR name LIKE ? ESCAPE '\\' OR organization LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' OR phone LIKE ? ESCAPE '\\') AND (?='' OR status=?)"
        params = (search, pattern, pattern, pattern, pattern, status, status)
        with self.auth.connection() as c:
            total = c.execute("SELECT count(*) FROM marketing_leads WHERE " + where, params).fetchone()[0]
            rows = c.execute("SELECT * FROM marketing_leads WHERE " + where + " ORDER BY created_at DESC,id LIMIT 25 OFFSET ?", (*params, (page - 1) * 25)).fetchall()
            counts = {key: 0 for key in STAGES}
            counts.update({r[0]: r[1] for r in c.execute("SELECT status,count(*) FROM marketing_leads GROUP BY status")})
        return {"leads": [self.record(row) for row in rows], "total": total, "page": page, "page_size": 25, "counts": counts, "stages": STAGES}

    def update(self, lead_id, data, actor):
        if not isinstance(data, dict) or set(data) != {"status", "notes", "revision"} or not isinstance(data["status"], str) or data["status"] not in STAGES:
            raise ValueError("Atualização inválida.")
        notes = text_field(data["notes"], "anotações", 4000)
        revision = data["revision"]
        if type(revision) is not int or revision < 1:
            raise ValueError("Atualize o pedido antes de salvar.")
        with self.auth.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM marketing_leads WHERE id=?", (lead_id,)).fetchone()
            if not row:
                raise LookupError("Pedido não encontrado.")
            if row["revision"] != revision:
                raise ValueError("Este pedido foi atualizado em outra tela. Feche e abra novamente.")
            c.execute("UPDATE marketing_leads SET status=?,notes=?,revision=revision+1,updated_at=? WHERE id=?", (data["status"], notes, utc_now(), lead_id))
            self.auth._audit(c, "marketing_lead_updated", actor_id=actor, details={"lead_id": lead_id, "from": row["status"], "to": data["status"]})


def store_for(auth):
    with _init_lock:
        if not hasattr(auth, "marketing_leads"):
            store = MarketingLeadStore(auth)
            store.initialize()
            auth.marketing_leads = store
        return auth.marketing_leads


def public_request(handler):
    if handler.path.split("?", 1)[0] != "/api/public/demo":
        return False
    if handler.command != "POST":
        handler.auth_json({"ok": False, "error": "Método não permitido."}, 405)
        return True
    if not handler.same_origin_request():
        handler.auth_json({"ok": False, "error": "Origem da solicitação inválida."}, 403)
        return True
    with _init_lock:
        if not hasattr(handler.server, "marketing_limiter"):
            handler.server.marketing_limiter = RequestLimiter(limit=120)
            handler.server.marketing_contact_limiter = RequestLimiter()
    # Proxied visitors may share a peer; use a broader peer cap and a stricter contact cap.
    if not handler.server.marketing_limiter.allow(handler.client_address[0]):
        handler.auth_json({"ok": False, "error": "Muitas tentativas. Aguarde alguns minutos."}, 429)
        return True
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 12000 or handler.headers.get_content_type() != "application/json":
            raise ValueError("Envie os dados em JSON válido.")
        data = json.loads(handler.rfile.read(length))
        values = validate_request(data)
        if not handler.server.marketing_contact_limiter.allow(values["email"]):
            handler.auth_json({"ok": False, "error": "Muitas tentativas para este contato. Aguarde alguns minutos."}, 429)
            return True
        store_for(handler.server.auth_store).submit(data)
        handler.auth_json({"ok": True}, 201)
    except (ValueError, UnicodeError) as exc:
        handler.auth_json({"ok": False, "error": "Pedido inválido." if isinstance(exc, (json.JSONDecodeError, UnicodeError)) else str(exc)}, 400)
    except sqlite3.OperationalError:
        handler.auth_json({"ok": False, "error": "Não foi possível registrar agora. Tente novamente."}, 503)
    return True


def master_request(handler, parsed):
    if not parsed.path.startswith("/api/master/marketing-leads"):
        return False
    if not handler.require_master_auth():
        return True
    try:
        store = store_for(handler.server.auth_store)
        if handler.command == "GET" and parsed.path == "/api/master/marketing-leads":
            query = parse_qs(parsed.query)
            result = store.listing(query.get("search", [""])[0], query.get("status", [""])[0], int(query.get("page", ["1"])[0]))
            handler.auth_json({"ok": True, **result})
            return True
        match = re.fullmatch(r"/api/master/marketing-leads/([a-f0-9]{32})", parsed.path)
        if handler.command != "POST" or not match:
            handler.auth_json({"ok": False, "error": "Rota não encontrada."}, 404)
            return True
        if not handler.same_origin_request():
            handler.auth_json({"ok": False, "error": "Origem da solicitação inválida."}, 403)
            return True
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 24000 or handler.headers.get_content_type() != "application/json":
            raise ValueError("Envie os dados em JSON válido.")
        store.update(match[1], json.loads(handler.rfile.read(length)), handler.current_user["id"])
        handler.auth_json({"ok": True})
    except LookupError as exc:
        handler.auth_json({"ok": False, "error": str(exc)}, 404)
    except (ValueError, UnicodeError) as exc:
        handler.auth_json({"ok": False, "error": "Dados inválidos." if isinstance(exc, (json.JSONDecodeError, UnicodeError)) else str(exc)}, 400)
    except sqlite3.OperationalError:
        handler.auth_json({"ok": False, "error": "Banco temporariamente indisponível."}, 503)
    return True
