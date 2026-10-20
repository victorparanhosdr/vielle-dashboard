"""Validated no-code flows and durable, transactionally advanced CRM automations."""
from datetime import datetime
import json
import re
import threading
import time
import uuid

from crm_access import pipeline_visible

KINDS = {"start", "message", "delay", "condition", "move", "note", "task", "tag", "field", "close", "form", "end"}
TERMINAL = {"done", "cancelled", "failed"}


def schema(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS crm_bots(id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL REFERENCES doctors(id),
            pipeline_id TEXT NOT NULL REFERENCES pipelines(id),name TEXT NOT NULL,draft TEXT NOT NULL,
            published TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'draft',revision INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,updated_at REAL NOT NULL,activated_at REAL NOT NULL DEFAULT 0,activated_by TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS bot_events(id INTEGER PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),
            kind TEXT NOT NULL,stage_id TEXT NOT NULL,channel_id TEXT NOT NULL DEFAULT '',message_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,root TEXT NOT NULL,hop INTEGER NOT NULL DEFAULT 0,processed INTEGER NOT NULL DEFAULT 0,
            unique_key TEXT NOT NULL UNIQUE);
        CREATE INDEX IF NOT EXISTS bot_events_pending ON bot_events(processed,id);
        CREATE TABLE IF NOT EXISTS bot_runs(id TEXT PRIMARY KEY,bot_id TEXT NOT NULL REFERENCES crm_bots(id),
            lead_id TEXT NOT NULL REFERENCES leads(id),event_key TEXT NOT NULL,definition TEXT NOT NULL,status TEXT NOT NULL,
            node TEXT NOT NULL,due_at REAL NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
            expected_stage TEXT NOT NULL,channel_id TEXT NOT NULL DEFAULT '',inbound_at REAL NOT NULL DEFAULT 0,
            message_id TEXT NOT NULL DEFAULT '',root TEXT NOT NULL,hop INTEGER NOT NULL DEFAULT 0,steps INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',UNIQUE(bot_id,event_key));
        CREATE INDEX IF NOT EXISTS bot_runs_due ON bot_runs(status,due_at);
        CREATE TABLE IF NOT EXISTS bot_steps(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL REFERENCES bot_runs(id),
            node TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS bot_tasks(id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),
            run_id TEXT NOT NULL REFERENCES bot_runs(id),title TEXT NOT NULL,due_at REAL NOT NULL,status TEXT NOT NULL DEFAULT 'open');
    """)
    if "bot_run_id" not in {r[1] for r in c.execute("PRAGMA table_info(messages)")}:
        c.execute("ALTER TABLE messages ADD COLUMN bot_run_id TEXT NOT NULL DEFAULT ''")


def emit(c, lead, kind, now, *, channel="", message="", root="", hop=0):
    """Explicit live events only; historical imports never enqueue automations."""
    row = c.execute("SELECT stage_id FROM leads WHERE id=? AND deleted_at=0", (lead,)).fetchone()
    if not row or hop > 5:
        return
    unique = message + ":" + lead if message else uuid.uuid4().hex
    c.execute("INSERT OR IGNORE INTO bot_events(lead_id,kind,stage_id,channel_id,message_id,created_at,root,hop,unique_key) VALUES(?,?,?,?,?,?,?,?,?)",
              (lead, kind, row[0], channel, message, now, root or uuid.uuid4().hex, hop, unique))


def clean(value, limit=160, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError("Texto do robo invalido.")
    return value.strip()


def number(value, low, high):
    if isinstance(value, bool):
        raise ValueError("Numero invalido no robo.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("Numero invalido no robo.") from None
    if not low <= value <= high:
        raise ValueError("Numero fora do intervalo permitido.")
    return value


def definition(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list) or not 2 <= len(raw["nodes"]) <= 80:
        raise ValueError("O fluxo precisa de 2 a 80 blocos.")
    trigger = raw.get("trigger", {})
    if not isinstance(trigger, dict) or trigger.get("kind", "manual") not in {"manual", "created", "stage", "incoming"}:
        raise ValueError("Gatilho invalido.")
    result = {"trigger": {"kind": trigger.get("kind", "manual"), "stage_id": clean(trigger.get("stage_id", ""), 32, False),
                          "delay_minutes": number(trigger.get("delay_minutes", 0), 0, 525600)},
              "stop_on_reply": raw.get("stop_on_reply", True) is not False, "nodes": []}
    ids = set()
    for item in raw["nodes"]:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise ValueError("Bloco nao suportado.")
        ident = clean(str(item.get("id", "")), 40)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", ident) or ident in ids:
            raise ValueError("Identificador de bloco duplicado ou invalido.")
        ids.add(ident)
        kind = item["kind"]
        source = item.get("data", {})
        if not isinstance(source, dict):
            raise ValueError("Configuracao de bloco invalida.")
        data = {}
        if kind in {"message", "note", "form"}:
            data["body"] = clean(source.get("body", ""), 2000, kind != "form")
        if kind in {"delay", "task"}:
            data["minutes"] = number(source.get("minutes", 60), 1, 525600)
        if kind == "task":
            data["title"] = clean(source.get("title", ""), 160)
        if kind == "move":
            data["stage_id"] = clean(source.get("stage_id", ""), 32, False)
        if kind in {"message", "form", "close"}:
            data["channel_id"] = clean(source.get("channel_id", ""), 32, False)
        if kind == "form":
            data["form_id"] = clean(source.get("form_id", ""), 32, False)
        if kind == "tag":
            data.update(label=clean(source.get("label", ""), 60), remove=source.get("remove") is True)
        if kind == "field":
            field = source.get("field", "title")
            if field not in {"title", "next_action", "value_cents"}:
                raise ValueError("Campo nao permitido.")
            value = source.get("value", "")
            if field == "value_cents":
                value = int(number(value, 0, 100000000))
            elif field == "next_action":
                datetime.strptime(value, "%Y-%m-%d")
            else:
                value = clean(value, 160)
            data.update(field=field, value=value)
        if kind == "condition":
            field = source.get("field", "message")
            if field not in {"message", "tag", "service_location", "value_cents", "opted_out"}:
                raise ValueError("Condicao nao suportada.")
            data.update(field=field, value=clean(str(source.get("value", "")), 200, False))
        links = item.get("next", {})
        if not isinstance(links, dict) or set(links) - ({"yes", "no"} if kind == "condition" else {"next"} if kind != "end" else set()):
            raise ValueError("Conexao invalida.")
        result["nodes"].append({"id": ident, "kind": kind, "data": data,
            "x": number(item.get("x", 0), -10000, 10000), "y": number(item.get("y", 0), -10000, 10000),
            "next": {key: clean(str(value), 40) for key, value in links.items()}})
    starts = [n for n in result["nodes"] if n["kind"] == "start"]
    if len(starts) != 1:
        raise ValueError("O fluxo deve ter um unico inicio.")
    visiting, visited = set(), set()
    nodes = {n["id"]: n for n in result["nodes"]}
    def walk(ident):
        if ident not in nodes or nodes[ident]["kind"] == "start" and visiting:
            raise ValueError("Conexao aponta para um bloco invalido.")
        if ident in visiting:
            raise ValueError("O fluxo possui um ciclo. Use pausas sem retornar a blocos anteriores.")
        if ident in visited:
            return
        visiting.add(ident)
        for target in nodes[ident]["next"].values():
            walk(target)
        visiting.remove(ident); visited.add(ident)
    walk(starts[0]["id"])
    for ident in nodes:
        if ident not in visited:
            walk(ident)
    return result


class BotStore:
    def __init__(self, crm):
        self.crm = crm

    def owned(self, c, ident, doctor):
        row = self.crm.owned(c, "crm_bots", ident, doctor)
        self.crm.owned(c, "pipelines", row["pipeline_id"], doctor)
        return row

    def form(self, c, ident, doctor, pipeline):
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crm_forms'").fetchone():
            raise ValueError("Crie e publique um formulario primeiro.")
        row = c.execute("SELECT * FROM crm_forms WHERE id=? AND doctor_id=?", (ident, doctor)).fetchone()
        if not row or row["institute_key"] != self.crm.row(c, "pipelines", pipeline)["institute_key"]:
            raise ValueError("Formulario indisponivel nesta atividade.")
        return dict(row)

    def validate_scope(self, c, info, pipeline, doctor, *, publish=False):
        stages = {r[0] for r in c.execute("SELECT id FROM stages WHERE pipeline_id=?", (pipeline,))}
        trigger = info["trigger"]
        if trigger["stage_id"] and trigger["stage_id"] not in stages:
            raise ValueError("A fase do gatilho nao pertence a este funil.")
        if publish and trigger["kind"] in {"stage", "created"} and not trigger["stage_id"]:
            raise ValueError("Escolha a fase de entrada do gatilho.")
        for node in info["nodes"]:
            data = node["data"]
            if data.get("stage_id") and data["stage_id"] not in stages:
                raise ValueError("A fase de destino nao pertence a este funil.")
            if data.get("channel_id"):
                self.crm.owned(c, "channels", data["channel_id"], doctor)
            if data.get("form_id"):
                form = self.form(c, data["form_id"], doctor, pipeline)
                if publish and form["status"] != "published":
                    raise ValueError("Publique o formulario antes de ativar o robo.")
            if not publish:
                continue
            if node["kind"] == "move" and not data.get("stage_id"):
                raise ValueError("Escolha a fase de destino.")
            if node["kind"] == "form" and not data.get("form_id"):
                raise ValueError("Escolha o formulario.")
            if node["kind"] in {"message", "form", "close"} and not data.get("channel_id") and trigger["kind"] != "incoming":
                raise ValueError("Escolha o numero WhatsApp nos blocos deste gatilho.")
            required = {"yes", "no"} if node["kind"] == "condition" else {"next"} if node["kind"] != "end" else set()
            if set(node["next"]) != required:
                raise ValueError("Conecte todas as saidas dos blocos antes de ativar.")
        if publish:
            nodes = {n["id"]: n for n in info["nodes"]}
            seen = set()
            def visit(node):
                if node["id"] in seen:
                    return
                seen.add(node["id"])
                for ident in node["next"].values():
                    visit(nodes[ident])
            visit(next(n for n in nodes.values() if n["kind"] == "start"))
            if len(seen) != len(nodes):
                raise ValueError("Ha blocos desconectados do inicio.")

    def detail(self, ident, doctor):
        with self.crm.db() as c:
            row = self.owned(c, ident, doctor)
        return {k: v for k, v in row.items() if k != "published"} | {"definition": json.loads(row["draft"])}

    def listing(self, doctor):
        with self.crm.db() as c:
            self.crm.row(c, "doctors", doctor)
            items = []
            for row in c.execute("""SELECT b.*,p.name pipeline_name,p.institute_key,
                (SELECT count(*) FROM bot_runs r WHERE r.bot_id=b.id) launches,
                (SELECT count(*) FROM bot_runs r WHERE r.bot_id=b.id AND r.status NOT IN ('done','cancelled','failed')) running
                FROM crm_bots b JOIN pipelines p ON p.id=b.pipeline_id WHERE b.doctor_id=? ORDER BY b.updated_at DESC""", (doctor,)):
                row = dict(row)
                if pipeline_visible(row):
                    draft, published = row.pop("draft"), row.pop("published")
                    info = json.loads(published if row["status"] == "active" else draft)
                    row["trigger"] = info["trigger"]; items.append(row)
            tasks = [dict(r) for r in c.execute("""SELECT t.*,l.doctor_id,l.pipeline_id,ct.name,p.institute_key FROM bot_tasks t
                JOIN leads l ON l.id=t.lead_id JOIN contacts ct ON ct.id=l.contact_id JOIN pipelines p ON p.id=l.pipeline_id
                WHERE l.doctor_id=? AND l.deleted_at=0 AND t.status='open' ORDER BY t.due_at LIMIT 200""", (doctor,)) if pipeline_visible(dict(r))]
        return {"bots": items, "tasks": tasks}

    def save(self, p, user):
        from crm_store import Conflict
        doctor, pipeline = p.get("doctor_id"), p.get("pipeline_id")
        ident, now = p.get("id") or uuid.uuid4().hex, time.time()
        info = definition(p.get("definition"))
        name = clean(p.get("name", "Novo robo"), 120)
        with self.crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            self.crm.owned(c, "pipelines", pipeline, doctor)
            self.validate_scope(c, info, pipeline, doctor)
            if p.get("id"):
                old = self.owned(c, ident, doctor)
                if old["revision"] != p.get("revision"):
                    raise Conflict("O robo foi alterado. Atualize antes de salvar.")
                if old["pipeline_id"] != pipeline:
                    raise ValueError("Crie outro robo para mudar de funil.")
                c.execute("UPDATE crm_bots SET name=?,draft=?,revision=revision+1,updated_at=? WHERE id=?", (name, json.dumps(info), now, ident))
            else:
                c.execute("INSERT INTO crm_bots(id,doctor_id,pipeline_id,name,draft,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                          (ident, doctor, pipeline, name, json.dumps(info), now, now))
        return self.detail(ident, doctor)

    def publish(self, ident, doctor, revision, active, user):
        from crm_store import Conflict
        now = time.time()
        with self.crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = self.owned(c, ident, doctor)
            if row["revision"] != revision:
                raise Conflict("O robo foi alterado. Atualize antes de publicar.")
            info = json.loads(row["draft"])
            if active:
                self.validate_scope(c, info, row["pipeline_id"], doctor, publish=True)
            c.execute("UPDATE crm_bots SET status=?,published=?,activated_at=?,activated_by=?,revision=revision+1,updated_at=? WHERE id=?",
                      ("active" if active else "paused", json.dumps(info) if active else row["published"], now, str(user), now, ident))
            self.cancel(c, "bot_id=?", (ident,), "Robo pausado ou nova versao publicada", now)

    @staticmethod
    def cancel(c, where, args, reason, now):
        c.execute("UPDATE messages SET status='cancelled',skip_reason=? WHERE status='queued' AND bot_run_id IN (SELECT id FROM bot_runs WHERE " + where + ")", (reason, *args))
        c.execute("UPDATE bot_runs SET status='cancelled',last_error=?,updated_at=? WHERE status NOT IN ('done','cancelled','failed') AND " + where, (reason, now, *args))

    def start(self, ident, doctor, lead, request):
        with self.crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            bot = self.owned(c, ident, doctor)
            target = self.crm.owned(c, "leads", lead, doctor)
            if bot["status"] != "active" or target["pipeline_id"] != bot["pipeline_id"]:
                raise ValueError("Ative um robo deste funil antes de iniciar.")
            key = "manual:" + clean(request, 80)
            old = c.execute("SELECT id,lead_id FROM bot_runs WHERE bot_id=? AND event_key=?", (ident, key)).fetchone()
            if old:
                if old["lead_id"] != lead:
                    raise ValueError("Identificador ja usado para outra oportunidade.")
                return old["id"]
            return self.enqueue(c, bot, target, key, time.time(), "", uuid.uuid4().hex, 0)

    def enqueue(self, c, bot, lead, event_key, now, channel, root, hop):
        if lead.get("deleted_at"):
            return ""
        if c.execute("SELECT 1 FROM contacts WHERE id=? AND opted_out=1", (lead["contact_id"],)).fetchone():
            return ""
        if c.execute("SELECT 1 FROM bot_runs WHERE bot_id=? AND lead_id=? AND status NOT IN ('done','cancelled','failed')", (bot["id"], lead["id"])).fetchone():
            return ""
        if c.execute("SELECT count(*) FROM bot_runs WHERE lead_id=? AND created_at>?", (lead["id"], now-86400)).fetchone()[0] >= 20:
            return ""
        info = json.loads(bot["published"])
        ident = uuid.uuid4().hex
        inbound = c.execute("SELECT coalesce(max(rowid),0) FROM messages WHERE contact_id=? AND direction='in'", (lead["contact_id"],)).fetchone()[0]
        start = next(n["id"] for n in info["nodes"] if n["kind"] == "start")
        c.execute("INSERT OR IGNORE INTO bot_runs(id,bot_id,lead_id,event_key,definition,status,node,due_at,created_at,updated_at,expected_stage,channel_id,inbound_at,root,hop) VALUES(?,?,?,?,?,'waiting',?,?,?,?,?,?,?,?,?)",
                  (ident, bot["id"], lead["id"], event_key, bot["published"], start, now+info["trigger"]["delay_minutes"]*60, now, now, lead["stage_id"], channel, inbound, root, hop))
        return ident

    def history(self, ident, doctor):
        with self.crm.db() as c:
            self.owned(c, ident, doctor)
            rows = [dict(r) for r in c.execute("SELECT r.*,ct.name,l.pipeline_id,l.doctor_id,p.institute_key FROM bot_runs r JOIN leads l ON l.id=r.lead_id JOIN contacts ct ON ct.id=l.contact_id JOIN pipelines p ON p.id=l.pipeline_id WHERE r.bot_id=? ORDER BY r.created_at DESC", (ident,)) if pipeline_visible(dict(r))][:100]
            for row in rows:
                row.pop("definition")
                row["steps_log"] = [dict(r) for r in c.execute("SELECT * FROM bot_steps WHERE run_id=? ORDER BY id", (row["id"],))]
        return rows

    @staticmethod
    def log(c, run, node, status, body, now):
        c.execute("INSERT INTO bot_steps(run_id,node,kind,status,body,created_at) VALUES(?,?,?,?,?,?)", (run, node["id"], node["kind"], status, body, now))

    def tick(self, now=None, permitted=lambda bot: True):
        now = time.time() if now is None else now
        with self.crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            for event in c.execute("SELECT * FROM bot_events WHERE processed=0 ORDER BY id LIMIT 100").fetchall():
                lead = dict(c.execute("SELECT * FROM leads WHERE id=?", (event["lead_id"],)).fetchone())
                if lead["deleted_at"]:
                    c.execute("UPDATE bot_events SET processed=1 WHERE id=?", (event["id"],))
                    continue
                for row in c.execute("SELECT * FROM crm_bots WHERE status='active' AND pipeline_id=? AND activated_at<=?", (lead["pipeline_id"], event["created_at"])).fetchall():
                    bot = dict(row); trigger = json.loads(bot["published"])["trigger"]
                    match = trigger["kind"] == event["kind"] or trigger["kind"] == "stage" and event["kind"] == "created"
                    if match and (not trigger["stage_id"] or trigger["stage_id"] == event["stage_id"]) and lead["stage_id"] == event["stage_id"] and permitted(bot):
                        self.enqueue(c, bot, lead, str(event["id"]), event["created_at"], event["channel_id"], event["root"], event["hop"])
                c.execute("UPDATE bot_events SET processed=1 WHERE id=?", (event["id"],))
            for row in c.execute("SELECT * FROM bot_runs WHERE status NOT IN ('done','cancelled','failed') ORDER BY due_at LIMIT 200").fetchall():
                run = dict(row)
                bot = dict(c.execute("SELECT * FROM crm_bots WHERE id=?", (run["bot_id"],)).fetchone())
                lead = dict(c.execute("SELECT l.*,ct.name,ct.phone,ct.opted_out,ct.service_location FROM leads l JOIN contacts ct ON ct.id=l.contact_id WHERE l.id=?", (run["lead_id"],)).fetchone())
                info = json.loads(run["definition"])
                inbound = c.execute("SELECT coalesce(max(rowid),0) FROM messages WHERE contact_id=? AND direction='in'", (lead["contact_id"],)).fetchone()[0]
                reason = ""
                if lead["deleted_at"]:
                    reason = "Oportunidade excluida para a lixeira"
                elif bot["status"] != "active" or not permitted(bot):
                    reason = "Robo pausado ou permissao removida"
                elif lead["opted_out"]:
                    reason = "Contato descadastrado"
                elif lead["stage_id"] != run["expected_stage"]:
                    reason = "Oportunidade mudou de fase"
                elif info["stop_on_reply"] and inbound > run["inbound_at"]:
                    reason = "Cliente respondeu; atendimento devolvido a equipe"
                if reason:
                    self.cancel(c, "id=?", (run["id"],), reason, now); continue
                if run["due_at"] > now:
                    continue
                node = next(n for n in info["nodes"] if n["id"] == run["node"])
                try:
                    self.step(c, bot, run, lead, node, now)
                except (ValueError, LookupError) as exc:
                    self.log(c, run["id"], node, "failed", str(exc), now)
                    c.execute("UPDATE bot_runs SET status='failed',last_error=?,updated_at=? WHERE id=?", (str(exc), now, run["id"]))

    def step(self, c, bot, run, lead, node, now):
        from crm_store import signed_message, TZ
        kind, data = node["kind"], node["data"]
        if run["steps"] >= 100:
            raise ValueError("Limite de passos atingido.")
        target = node["next"].get("next", "")
        if run["status"] == "sending":
            message = c.execute("SELECT status FROM messages WHERE id=?", (run["message_id"],)).fetchone()
            if message[0] in {"queued", "sending"}:
                return
            if message[0] != "sent":
                raise ValueError("Envio sem confirmacao ou bloqueado. Nao sera reenviado automaticamente.")
        elif kind in {"message", "form"}:
            channel = data.get("channel_id") or run["channel_id"]
            if not channel:
                raise ValueError("Escolha um numero no bloco para iniciar fora de uma conversa.")
            ch = self.crm.owned(c, "channels", channel, bot["doctor_id"])
            if ch["state"] != "open":
                raise ValueError("Numero WhatsApp desconectado.")
            body = data.get("body", "").replace("{{nome}}", lead.get("source_name") or lead["name"]).replace("{{primeiro_nome}}", (lead.get("source_name") or lead["name"]).split()[0])
            if kind == "form":
                from crm_form_integrations import base_url
                form = self.form(c, data["form_id"], bot["doctor_id"], bot["pipeline_id"])
                if form["status"] != "published":
                    raise ValueError("Formulario pausado ou indisponivel.")
                body += "\n" + base_url() + "/f/" + form["slug"]
            body = signed_message(body.strip() + "\n\nPara nao receber mais mensagens, responda SAIR.", "Assistente virtual")
            ident = uuid.uuid4().hex
            c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,created_at,recipient_phone,sender_name,sender_user_id,origin,bot_run_id) VALUES(?,?,?,?,'out',?,?,?,?,'bot',?)",
                      (ident, channel, lead["contact_id"], body, now, lead["phone"], "Assistente virtual", bot["activated_by"], run["id"]))
            c.execute("UPDATE bot_runs SET status='sending',message_id=?,channel_id=?,updated_at=? WHERE id=?", (ident, channel, now, run["id"]))
            self.log(c, run["id"], node, "queued", "Mensagem na fila WhatsApp", now)
            return
        elif kind == "delay":
            c.execute("UPDATE bot_runs SET node=?,due_at=?,status='waiting',steps=steps+1,updated_at=? WHERE id=?", (target, now+data["minutes"]*60, now, run["id"]))
            self.log(c, run["id"], node, "done", "Pausa programada", now); return
        elif kind == "condition":
            field, value = data["field"], data["value"]
            if field == "message":
                last = c.execute("SELECT body FROM messages WHERE contact_id=? AND direction='in' ORDER BY created_at DESC LIMIT 1", (lead["contact_id"],)).fetchone()
                match = bool(last and value.casefold() in last[0].casefold())
            elif field == "tag":
                match = bool(c.execute("SELECT 1 FROM contact_tags WHERE doctor_id=? AND contact_id=? AND label=?", (bot["doctor_id"], lead["contact_id"], value)).fetchone())
            elif field == "value_cents":
                match = lead["value_cents"] >= number(value, 0, 100000000)
            else:
                match = str(lead[field]) == value
            target = node["next"]["yes" if match else "no"]
        elif kind == "move":
            stage = self.crm.row(c, "stages", data["stage_id"])
            if stage["pipeline_id"] != lead["pipeline_id"]:
                raise ValueError("Fase de outro funil.")
            c.execute("UPDATE leads SET stage_id=?,revision=revision+1,updated_at=? WHERE id=?", (stage["id"], now, lead["id"]))
            c.execute("UPDATE bot_runs SET expected_stage=? WHERE id=?", (stage["id"], run["id"]))
            self.crm.event(c, lead["id"], "Robo: " + bot["name"], "stage", stage["name"], now)
            if stage["id"] != lead["stage_id"]:
                emit(c, lead["id"], "stage", now, root=run["root"], hop=run["hop"]+1)
        elif kind == "note":
            self.crm.event(c, lead["id"], "Robo: " + bot["name"], "note", data["body"], now)
        elif kind == "task":
            due = now + data["minutes"]*60
            c.execute("INSERT INTO bot_tasks(id,lead_id,run_id,title,due_at) VALUES(?,?,?,?,?)", (uuid.uuid4().hex, lead["id"], run["id"], data["title"], due))
            self.crm.event(c, lead["id"], "Robo: " + bot["name"], "note", "Tarefa: " + data["title"] + " | Prazo: " + datetime.fromtimestamp(due,TZ).isoformat(timespec="minutes"), now)
        elif kind == "tag":
            if data["remove"]:
                c.execute("DELETE FROM contact_tags WHERE doctor_id=? AND contact_id=? AND label=?", (bot["doctor_id"], lead["contact_id"], data["label"]))
            else:
                c.execute("INSERT OR IGNORE INTO contact_tags VALUES(?,?,?,?)", (bot["doctor_id"], lead["contact_id"], data["label"], now))
        elif kind == "field":
            c.execute("UPDATE leads SET " + data["field"] + "=?,revision=revision+1,updated_at=? WHERE id=?", (data["value"], now, lead["id"]))
        elif kind == "close":
            channel = data.get("channel_id") or run["channel_id"]
            self.crm.owned(c, "channels", channel, bot["doctor_id"])
            marker = c.execute("SELECT coalesce(max(rowid),0) FROM messages WHERE contact_id=? AND channel_id=? AND direction='in'", (lead["contact_id"], channel)).fetchone()[0]
            self.crm.resolve_conversation(c, channel, lead["contact_id"], marker, "closed", "bot:" + bot["id"])
        self.log(c, run["id"], node, "done", "Sim" if kind == "condition" and target == node["next"]["yes"] else "Nao" if kind == "condition" else "Concluido", now)
        c.execute("UPDATE bot_runs SET node=?,status=?,due_at=?,updated_at=?,steps=steps+1,message_id='' WHERE id=?", (target or node["id"], "done" if kind == "end" else "waiting", now, now, run["id"]))


def message_allowed(c, run_id):
    row = c.execute("""SELECT r.status,r.expected_stage,r.definition,r.inbound_at,l.stage_id,l.contact_id,b.status bot_status FROM bot_runs r
        JOIN leads l ON l.id=r.lead_id JOIN crm_bots b ON b.id=r.bot_id WHERE r.id=?""", (run_id,)).fetchone()
    if not row or row["status"] != "sending" or row["bot_status"] != "active" or row["stage_id"] != row["expected_stage"]:
        return False
    if json.loads(row["definition"])["stop_on_reply"]:
        latest = c.execute("SELECT coalesce(max(rowid),0) FROM messages WHERE contact_id=? AND direction='in'", (row["contact_id"],)).fetchone()[0]
        if latest > row["inbound_at"]:
            return False
    return True


def run_worker(auth, stop):
    from crm_api import store_for
    crm = store_for(auth); bots = BotStore(crm)
    def permitted(bot):
        user = auth.get_public_user(bot["activated_by"])
        if not user or user.get("status") != "active" or not (user.get("is_master") or auth.has_permission(user["id"], "vielle", "crm.view")):
            return False
        with crm.db() as c:
            pipeline = dict(c.execute("SELECT * FROM pipelines WHERE id=?", (bot["pipeline_id"],)).fetchone())
        return not pipeline["institute_key"] or pipeline["institute_key"] in crm.course_source.store.allowed(user)
    while not stop.wait(2):
        try:
            bots.tick(permitted=permitted)
        except Exception:
            import logging
            logging.exception("CRM automation tick failed")
