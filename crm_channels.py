"""Internal WhatsApp connection labels and explicit routing for new leads."""


def schema(c):
    columns = {r[1] for r in c.execute("PRAGMA table_info(channels)")}
    for name, definition in (("inbound_pipeline_id", "TEXT NOT NULL DEFAULT ''"),
                             ("inbound_stage_id", "TEXT NOT NULL DEFAULT ''"),
                             ("config_revision", "INTEGER NOT NULL DEFAULT 1")):
        if name not in columns:
            c.execute(f"ALTER TABLE channels ADD COLUMN {name} {definition}")


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
        if type(payload.get("config_revision")) is not int or payload["config_revision"] != channel["config_revision"]:
            raise Conflict("A conexao foi alterada em outra tela. Atualize antes de salvar.")
        name = text(payload.get("name"))
        pipeline, stage = validate_target(store, c, channel["doctor_id"],
            payload.get("inbound_pipeline_id", ""), payload.get("inbound_stage_id", ""))
        c.execute("UPDATE channels SET name=?,inbound_pipeline_id=?,inbound_stage_id=?,config_revision=config_revision+1 WHERE id=?",
                  (name, pipeline, stage, channel["id"]))
        return {"id": channel["id"]}


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
