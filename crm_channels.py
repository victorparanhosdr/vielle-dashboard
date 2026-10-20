"""Internal WhatsApp connection labels, routing, and audited ownership transfers."""
import json
import time
import uuid


def schema(c):
    columns = {r[1] for r in c.execute("PRAGMA table_info(channels)")}
    for name, definition in (("inbound_pipeline_id", "TEXT NOT NULL DEFAULT ''"),
                             ("inbound_stage_id", "TEXT NOT NULL DEFAULT ''"),
                             ("config_revision", "INTEGER NOT NULL DEFAULT 1"),
                             ("archived_at", "REAL NOT NULL DEFAULT 0"),
                             ("archived_by", "TEXT NOT NULL DEFAULT ''")):
        if name not in columns:
            c.execute(f"ALTER TABLE channels ADD COLUMN {name} {definition}")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_transfers(
        id TEXT PRIMARY KEY,channel_id TEXT NOT NULL REFERENCES channels(id),
        from_doctor_id TEXT NOT NULL REFERENCES doctors(id),to_doctor_id TEXT NOT NULL REFERENCES doctors(id),
        actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,created_at REAL NOT NULL,
        before_config TEXT NOT NULL,after_config TEXT NOT NULL,impact TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_lifecycle(
        id TEXT PRIMARY KEY,channel_id TEXT NOT NULL REFERENCES channels(id),
        doctor_id TEXT NOT NULL REFERENCES doctors(id),action TEXT NOT NULL,
        actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,created_at REAL NOT NULL,
        before_config TEXT NOT NULL,after_config TEXT NOT NULL,impact TEXT NOT NULL)""")


def require_active(channel):
    if channel["archived_at"]:
        raise ValueError("Numero removido do CRM. Restaure a conexao em Numeros antes de utiliza-la.")


def validate_target(store, c, doctor, pipeline, stage):
    if not isinstance(pipeline, str) or not isinstance(stage, str) or stage and not pipeline:
        raise ValueError("Escolha um funil e uma etapa de entrada validos.")
    if not pipeline:
        return "", ""
    store.owned(c, "pipelines", pipeline, doctor)
    row = store.row(c, "stages", stage) if stage else c.execute(
        "SELECT * FROM stages WHERE pipeline_id=? AND kind='open' ORDER BY position LIMIT 1", (pipeline,)).fetchone()
    if not row or row["pipeline_id"] != pipeline or row["kind"] != "open":
        raise ValueError("Escolha uma etapa aberta do funil selecionado.")
    return pipeline, row["id"]


def update(store, payload):
    from crm_store import Conflict, text
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        channel = store.owned(c, "channels", payload.get("id"), payload.get("doctor_id"))
        require_active(channel)
        if type(payload.get("config_revision")) is not int or payload["config_revision"] != channel["config_revision"]:
            raise Conflict("A conexao foi alterada em outra tela. Atualize antes de salvar.")
        name = text(payload.get("name"))
        pipeline, stage = validate_target(store, c, channel["doctor_id"],
            payload.get("inbound_pipeline_id", ""), payload.get("inbound_stage_id", ""))
        c.execute("UPDATE channels SET name=?,inbound_pipeline_id=?,inbound_stage_id=?,config_revision=config_revision+1 WHERE id=?",
                  (name, pipeline, stage, channel["id"]))
        return {"id": channel["id"]}


def _references_channel(raw, channel):
    return any(node.get("data", {}).get("channel_id") == channel for node in json.loads(raw or "{}").get("nodes", []))


def _impact(c, channel):
    related = """(channel_id=? OR id IN (SELECT message_id FROM reply_requests WHERE source_channel_id=?))"""
    messages = c.execute("SELECT status,count(*) FROM messages WHERE " + related + " GROUP BY status", (channel, channel)).fetchall()
    statuses = {row[0]: row[1] for row in messages}
    bots = [r["id"] for r in c.execute("SELECT * FROM crm_bots WHERE status='active'")
            if _references_channel(r["published"], channel)]
    return {"conversations": c.execute("SELECT count(DISTINCT contact_id) FROM messages WHERE channel_id=?", (channel,)).fetchone()[0],
            "messages": c.execute("SELECT count(*) FROM messages WHERE channel_id=?", (channel,)).fetchone()[0],
            "queued": statuses.get("queued", 0), "sending": statuses.get("sending", 0), "unknown": statuses.get("unknown", 0),
            "campaigns": c.execute("SELECT count(*) FROM campaigns WHERE channel_id=? AND status IN ('draft','running','paused')", (channel,)).fetchone()[0],
            "bots": len(bots)}


def transfer_options(store, ident, doctor):
    with store.db() as c:
        channel = store.owned(c, "channels", ident, doctor)
        require_active(channel)
        doctors = [dict(r) for r in c.execute("SELECT * FROM doctors WHERE id<>? ORDER BY name", (doctor,))]
        pipelines = [dict(r) for r in c.execute("SELECT id,doctor_id,name FROM pipelines WHERE doctor_id<>? ORDER BY name", (doctor,))]
        for pipeline in pipelines:
            pipeline["stages"] = [dict(r) for r in c.execute("SELECT id,name FROM stages WHERE pipeline_id=? AND kind='open' ORDER BY position", (pipeline["id"],))]
        return {"channel": channel, "doctors": doctors, "pipelines": pipelines, "impact": _impact(c, ident)}


def _stop_activity(c, channel, reason, now):
    from crm_bots import BotStore
    related = """(channel_id=? OR id IN (SELECT message_id FROM reply_requests WHERE source_channel_id=?))"""
    c.execute("UPDATE messages SET status='cancelled',skip_reason=? WHERE status='queued' AND " + related,
              (reason, channel, channel))
    c.execute("UPDATE campaigns SET status='paused',last_error=? WHERE channel_id=? AND status IN ('draft','running','paused')", (reason, channel))
    paused_bots = set()
    for bot in c.execute("SELECT * FROM crm_bots WHERE status='active'").fetchall():
        if _references_channel(bot["published"], channel):
            c.execute("UPDATE crm_bots SET status='paused',revision=revision+1,updated_at=? WHERE id=?", (now, bot["id"]))
            paused_bots.add(bot["id"])
    for run in c.execute("SELECT * FROM bot_runs WHERE status NOT IN ('done','cancelled','failed')").fetchall():
        if run["bot_id"] in paused_bots or run["channel_id"] == channel or _references_channel(run["definition"], channel):
            BotStore.cancel(c, "id=?", (run["id"],), reason, now)
    c.execute("UPDATE bot_events SET processed=1 WHERE channel_id=? AND processed=0", (channel,))


def removal_options(store, ident, doctor):
    with store.db() as c:
        channel = store.owned(c, "channels", ident, doctor)
        require_active(channel)
        return {"channel": channel, "impact": _impact(c, ident)}


def lifecycle(store, payload, actor_id, actor_name, *, restore=False):
    from crm_store import Conflict
    if payload.get("confirm") is not True:
        raise ValueError("Confirme a restauracao do numero." if restore else "Confirme a remocao do numero, preservando o historico.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        channel = store.owned(c, "channels", payload.get("id"), payload.get("doctor_id"))
        if type(payload.get("config_revision")) is not int or payload["config_revision"] != channel["config_revision"]:
            raise Conflict("A conexao foi alterada em outra tela. Atualize antes de continuar.")
        if restore:
            if not channel["archived_at"]:
                raise ValueError("Este numero nao foi removido.")
        else:
            require_active(channel)
        impact = _impact(c, channel["id"])
        if not restore and (impact["sending"] or impact["unknown"]):
            raise Conflict("Ha mensagem em envio ou sem confirmacao. Confira os envios antes de remover o numero.")
        now = time.time()
        if not restore:
            _stop_activity(c, channel["id"], "Numero removido do CRM", now)
        before = {key: channel[key] for key in ("state", "archived_at", "archived_by")}
        after = {"state": "close" if restore else "archived", "archived_at": 0 if restore else now,
                 "archived_by": "" if restore else str(actor_id)}
        c.execute("UPDATE channels SET state=?,archived_at=?,archived_by=?,config_revision=config_revision+1 WHERE id=?",
                  (after["state"], after["archived_at"], after["archived_by"], channel["id"]))
        event = uuid.uuid4().hex
        c.execute("INSERT INTO channel_lifecycle VALUES(?,?,?,?,?,?,?,?,?,?)", (event, channel["id"], channel["doctor_id"],
            "restore" if restore else "remove", str(actor_id), actor_name, now, json.dumps(before), json.dumps(after), json.dumps(impact)))
        return {"id": channel["id"], "event_id": event, "impact": impact, **after}


def transfer(store, payload, actor_id, actor_name):
    from crm_store import Conflict
    if payload.get("confirm") is not True:
        raise ValueError("Confirme a transferencia da conexao e do historico para o novo profissional.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        channel = store.owned(c, "channels", payload.get("id"), payload.get("doctor_id"))
        require_active(channel)
        if type(payload.get("config_revision")) is not int or payload["config_revision"] != channel["config_revision"]:
            raise Conflict("A conexao foi alterada em outra tela. Atualize antes de transferir.")
        target = store.row(c, "doctors", payload.get("target_doctor_id"))
        if target["id"] == channel["doctor_id"]:
            raise ValueError("Escolha outro profissional para receber esta conexao.")
        if not payload.get("inbound_pipeline_id"):
            raise ValueError("Escolha o funil de destino dos novos leads.")
        pipeline, stage = validate_target(store, c, target["id"], payload.get("inbound_pipeline_id"), payload.get("inbound_stage_id", ""))
        impact = _impact(c, channel["id"])
        if impact["sending"] or impact["unknown"]:
            raise Conflict("Ha mensagem em envio ou sem confirmacao nesta conexao. Confira os envios antes de transferir.")
        now, reason = time.time(), "Conexao transferida para outro profissional"
        _stop_activity(c, channel["id"], reason, now)
        # Bound attachments follow their actual conversation; unsent personal drafts do not move.
        c.execute("""UPDATE message_media SET source_channel_id=(SELECT channel_id FROM messages WHERE id=message_media.message_id)
            WHERE source_channel_id=? AND message_id IN (SELECT id FROM messages WHERE channel_id<>?)""", (channel["id"], channel["id"]))
        c.execute("""UPDATE message_media SET doctor_id=?,source_channel_id=?
            WHERE message_id IN (SELECT id FROM messages WHERE channel_id=?)""", (target["id"], channel["id"], channel["id"]))
        after = {"doctor_id": target["id"], "inbound_pipeline_id": pipeline, "inbound_stage_id": stage}
        before = {key: channel[key] for key in after}
        c.execute("UPDATE channels SET doctor_id=?,inbound_pipeline_id=?,inbound_stage_id=?,config_revision=config_revision+1 WHERE id=?",
                  (target["id"], pipeline, stage, channel["id"]))
        transfer_id = uuid.uuid4().hex
        c.execute("INSERT INTO channel_transfers VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (transfer_id, channel["id"], channel["doctor_id"], target["id"], str(actor_id), actor_name, now,
                   json.dumps(before), json.dumps(after), json.dumps(impact)))
        return {"id": channel["id"], "transfer_id": transfer_id, **after, "impact": impact}


def inbound_target(c, channel):
    channel = dict(channel)
    if channel.get("inbound_pipeline_id"):
        row = c.execute("""SELECT s.id,s.pipeline_id,p.course_key FROM stages s JOIN pipelines p ON p.id=s.pipeline_id
            WHERE p.doctor_id=? AND p.id=? AND s.id=? AND s.kind='open'""",
            (channel["doctor_id"], channel["inbound_pipeline_id"], channel["inbound_stage_id"])).fetchone()
    else:
        row = c.execute("""SELECT s.id,s.pipeline_id,p.course_key FROM stages s JOIN pipelines p ON p.id=s.pipeline_id
            WHERE p.doctor_id=? AND s.kind='open' ORDER BY p.name,s.position LIMIT 1""", (channel["doctor_id"],)).fetchone()
    return dict(row) if row else None
