"""Frozen, atomic lead operations and recoverable deletion. No provider requests."""
import json
import time
import uuid

from crm_access import check_row

MAX_BATCH = 5000


def schema(c):
    columns = {r[1] for r in c.execute("PRAGMA table_info(leads)")}
    for name, definition in (("deleted_at", "REAL NOT NULL DEFAULT 0"), ("deleted_by", "TEXT NOT NULL DEFAULT ''")):
        if name not in columns:
            c.execute(f"ALTER TABLE leads ADD COLUMN {name} {definition}")
    c.execute("CREATE INDEX IF NOT EXISTS leads_trash ON leads(doctor_id,pipeline_id,deleted_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS lead_bulk_batches(
        id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL,pipeline_id TEXT NOT NULL,actor_id TEXT NOT NULL,
        action TEXT NOT NULL,target_stage TEXT NOT NULL,pipeline_revision INTEGER NOT NULL,
        items TEXT NOT NULL,automations TEXT NOT NULL,expires_at REAL NOT NULL,result TEXT NOT NULL DEFAULT '')""")
    columns = {r[1] for r in c.execute("PRAGMA table_info(lead_bulk_batches)")}
    for name, definition in (("target_doctor_id", "TEXT NOT NULL DEFAULT ''"),
                             ("target_pipeline_id", "TEXT NOT NULL DEFAULT ''"),
                             ("target_pipeline_revision", "INTEGER NOT NULL DEFAULT 0")):
        if name not in columns:
            c.execute(f"ALTER TABLE lead_bulk_batches ADD COLUMN {name} {definition}")
    c.execute("""CREATE TABLE IF NOT EXISTS lead_transfers(
        id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,lead_id TEXT NOT NULL REFERENCES leads(id),
        contact_id TEXT NOT NULL REFERENCES contacts(id),from_doctor_id TEXT NOT NULL,
        from_pipeline_id TEXT NOT NULL,from_stage_id TEXT NOT NULL,to_doctor_id TEXT NOT NULL,
        to_pipeline_id TEXT NOT NULL,to_stage_id TEXT NOT NULL,actor_id TEXT NOT NULL,
        actor_name TEXT NOT NULL,created_at REAL NOT NULL,original TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS lead_transfer_origin ON lead_transfers(from_doctor_id,contact_id)")


def destinations(store, doctor):
    from crm_access import doctor_visible, pipeline_visible
    with store.db() as c:
        store.row(c, "doctors", doctor)
        pipelines = [dict(r) for r in c.execute("SELECT * FROM pipelines ORDER BY name") if pipeline_visible(dict(r))]
        for pipeline in pipelines:
            pipeline["stages"] = [dict(r) for r in c.execute("SELECT * FROM stages WHERE pipeline_id=? ORDER BY position", (pipeline["id"],))]
        owners = {p["doctor_id"] for p in pipelines if p["stages"]}
        doctors = [dict(r) for r in c.execute("SELECT * FROM doctors ORDER BY name") if r["id"] in owners and doctor_visible(r["id"])]
        return {"doctors": doctors, "pipelines": [p for p in pipelines if p["stages"]]}


def transfer_checks(c, rows, target):
    from crm_store import Conflict
    for row in rows:
        if c.execute("SELECT 1 FROM leads WHERE contact_id=? AND pipeline_id=? AND id<>?",
                     (row["contact_id"], target["id"], row["id"])).fetchone():
            raise Conflict("Um contato selecionado ja possui oportunidade no funil de destino, inclusive na lixeira. Nenhum lead foi movido para evitar duplicidade.")
        if c.execute("""SELECT 1 FROM messages m LEFT JOIN campaigns ca ON ca.id=m.campaign_id
            WHERE m.status IN ('sending','unknown') AND
            (m.bot_run_id IN (SELECT id FROM bot_runs WHERE lead_id=?) OR (ca.doctor_id=? AND m.contact_id=?)) LIMIT 1""",
            (row["id"], row["doctor_id"], row["contact_id"])).fetchone():
            raise Conflict("Ha envio automatico em andamento ou sem confirmacao para um contato selecionado. Confira antes de transferir.")


def lead(c, store, ident, doctor, pipeline, deleted=False):
    row = c.execute("SELECT * FROM leads WHERE id=?", (ident,)).fetchone()
    if not row:
        raise LookupError("Oportunidade nao encontrada.")
    row = dict(row)
    check_row(c, "leads", row)
    if row["doctor_id"] != doctor or row["pipeline_id"] != pipeline or bool(row["deleted_at"]) != deleted:
        raise ValueError("Oportunidade fora da selecao. Atualize o funil.")
    return row


def automations(c, pipeline, stage):
    result = []
    for row in c.execute("SELECT id,name,revision,published FROM crm_bots WHERE pipeline_id=? AND status='active' ORDER BY id", (pipeline,)):
        trigger = json.loads(row["published"])["trigger"]
        if trigger["kind"] == "stage" and (not trigger["stage_id"] or trigger["stage_id"] == stage):
            result.append({"id": row["id"], "name": row["name"], "revision": row["revision"]})
    return result


def column_selection(store, payload):
    doctor, pipeline, stage = payload.get("doctor_id"), payload.get("pipeline_id"), payload.get("stage_id")
    with store.db() as c:
        store.owned(c, "pipelines", pipeline, doctor)
        row = store.row(c, "stages", stage)
        if row["pipeline_id"] != pipeline:
            raise ValueError("A coluna nao pertence ao funil selecionado.")
    from crm_campaigns import selection
    rows, _, _, _ = selection(store, {"doctor_id": doctor, "pipeline_id": pipeline, "pipeline_ids": [pipeline],
        "stage_ids": [stage], "stages_explicit": True, "filters": payload.get("filters", {})})
    if len(rows) > MAX_BATCH:
        raise ValueError(f"Esta coluna tem {len(rows)} leads. Refine os filtros para selecionar ate {MAX_BATCH} por operacao.")
    return {"stage_id": stage, "lead_ids": [row["id"] for row in rows], "count": len(rows)}


def preview(store, payload, actor):
    action, doctor, pipeline = payload.get("action"), payload.get("doctor_id"), payload.get("pipeline_id")
    if action not in {"move", "transfer", "delete", "restore"}:
        raise ValueError("Acao em massa invalida.")
    selection = payload.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("Selecione as oportunidades.")
    with store.db() as c:
        owner = store.owned(c, "pipelines", pipeline, doctor)
        destination = store.owned(c, "pipelines", payload.get("target_pipeline_id"), payload.get("target_doctor_id")) if action == "transfer" else owner
        if action == "transfer" and destination["id"] == pipeline:
            raise ValueError("Escolha outro funil ou use Mover etapa no funil atual.")
        target = store.row(c, "stages", payload.get("stage_id")) if action in {"move", "transfer"} else None
        if target and target["pipeline_id"] != destination["id"]:
            raise ValueError("A etapa de destino deve pertencer ao mesmo funil.")
    if action == "restore":
        ids = selection.get("lead_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BATCH or any(not isinstance(i, str) for i in ids):
            raise ValueError("Selecione os leads da lixeira.")
        with store.db() as c:
            rows = [lead(c, store, ident, doctor, pipeline, True) for ident in dict.fromkeys(ids)]
            if any(c.execute("SELECT merged_into FROM contacts WHERE id=?", (r["contact_id"],)).fetchone()[0] for r in rows):
                raise ValueError("Este cadastro foi consolidado com outro paciente. O historico esta preservado na oportunidade principal.")
    else:
        from crm_campaigns import selection as select_leads
        rows, _, _, _ = select_leads(store, {**selection, "doctor_id": doctor, "pipeline_id": pipeline, "pipeline_ids": [pipeline]})
    if not rows or len(rows) > MAX_BATCH:
        raise ValueError(f"Selecione entre 1 e {MAX_BATCH} oportunidades por operacao.")
    now, ident = time.time(), uuid.uuid4().hex
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        current = store.owned(c, "pipelines", pipeline, doctor)
        if current["revision"] != owner["revision"]:
            raise ValueError("O funil mudou. Atualize a selecao.")
        destination = store.owned(c, "pipelines", destination["id"], destination["doctor_id"])
        if target:
            target = store.row(c, "stages", target["id"])
            if target["pipeline_id"] != destination["id"]:
                raise ValueError("A etapa nao pertence ao funil de destino.")
        frozen, sample = [], []
        for row in rows:
            item = lead(c, store, row["id"], doctor, pipeline, action == "restore")
            if item["revision"] != row["revision"]:
                from crm_store import Conflict
                raise Conflict("Uma oportunidade mudou durante a selecao. Confira novamente.")
            if action == "move" and item["stage_id"] == target["id"]:
                continue
            frozen.append({"id": item["id"], "revision": item["revision"]})
            if len(sample) < 10:
                name = c.execute("SELECT name FROM contacts WHERE id=?", (item["contact_id"],)).fetchone()[0]
                sample.append({"id": item["id"], "name": name, "title": item["title"]})
        if not frozen:
            raise ValueError("Os leads selecionados ja estao nessa etapa.")
        if action == "transfer":
            transfer_checks(c, [lead(c, store, item["id"], doctor, pipeline) for item in frozen], destination)
        bots = automations(c, pipeline, target["id"]) if action == "move" else []
        c.execute("DELETE FROM lead_bulk_batches WHERE expires_at<? AND result=''", (now-86400,))
        c.execute("INSERT INTO lead_bulk_batches(id,doctor_id,pipeline_id,actor_id,action,target_stage,pipeline_revision,items,automations,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ident, doctor, pipeline, str(actor["id"]), action, target["id"] if target else "", owner["revision"],
             json.dumps(frozen), json.dumps(bots), now+900))
        if action == "transfer":
            c.execute("UPDATE lead_bulk_batches SET target_doctor_id=?,target_pipeline_id=?,target_pipeline_revision=? WHERE id=?",
                      (destination["doctor_id"], destination["id"], destination["revision"], ident))
        target_doctor = store.row(c, "doctors", destination["doctor_id"])
    return {"id": ident, "action": action, "count": len(frozen), "selected": len(rows), "sample": sample,
            "pipeline": owner["name"], "stage": target["name"] if target else "", "automations": bots,
            "target_doctor": target_doctor["name"], "target_pipeline": destination["name"]}


def stop_automations(c, rows, now, reason="Oportunidade excluida para a lixeira"):
    from crm_bots import BotStore
    for row in rows:
        BotStore.cancel(c, "lead_id=?", (row["id"],), reason, now)
        c.execute("UPDATE bot_tasks SET status='cancelled' WHERE lead_id=? AND status='open'", (row["id"],))
    deleted = {row["id"]: row for row in rows}
    # Frozen campaign audiences retain the exact opportunity that was selected.
    for snapshot in c.execute("SELECT campaign_id,payload FROM campaign_audiences WHERE campaign_id<>'' AND doctor_id=?", (rows[0]["doctor_id"],)).fetchall():
        ids = set(json.loads(snapshot["payload"]).get("ids", [])) & deleted.keys()
        for ident in ids:
            c.execute("UPDATE messages SET status='skipped',skip_reason=? WHERE campaign_id=? AND contact_id=? AND status='queued'",
                      (reason, snapshot["campaign_id"], deleted[ident]["contact_id"]))


def apply(store, payload, actor):
    from crm_store import Conflict
    if payload.get("confirm") is not True:
        raise ValueError("Confirme a acao sobre os leads selecionados.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        batch = c.execute("SELECT * FROM lead_bulk_batches WHERE id=?", (payload.get("id"),)).fetchone()
        if not batch or batch["actor_id"] != str(actor["id"]) or batch["doctor_id"] != payload.get("doctor_id"):
            raise LookupError("Selecao indisponivel.")
        pipeline = store.owned(c, "pipelines", batch["pipeline_id"], batch["doctor_id"])
        destination = store.owned(c, "pipelines", batch["target_pipeline_id"], batch["target_doctor_id"]) if batch["action"] == "transfer" else pipeline
        frozen = json.loads(batch["items"])
        if batch["result"]:
            # Recheck access even for an idempotent response after a role change.
            for item in frozen:
                row = c.execute("SELECT * FROM leads WHERE id=?", (item["id"],)).fetchone()
                if row:
                    check_row(c, "leads", dict(row))
            return json.loads(batch["result"])
        if batch["expires_at"] < time.time() or pipeline["revision"] != batch["pipeline_revision"]:
            raise Conflict("A selecao expirou ou o funil mudou. Confira novamente.")
        if batch["action"] == "transfer" and destination["revision"] != batch["target_pipeline_revision"]:
            raise Conflict("O funil de destino mudou. Confira a transferencia novamente.")
        action, now = batch["action"], time.time()
        rows = [lead(c, store, item["id"], batch["doctor_id"], batch["pipeline_id"], action == "restore") for item in frozen]
        if any(row["revision"] != item["revision"] for row, item in zip(rows, frozen)):
            raise Conflict("Um lead foi alterado por outra pessoa. Nenhuma mudanca foi aplicada. Atualize a selecao.")
        target = store.row(c, "stages", batch["target_stage"]) if action in {"move", "transfer"} else None
        if target and target["pipeline_id"] != destination["id"]:
            raise Conflict("A etapa de destino mudou. Confira novamente.")
        if action == "move" and automations(c, pipeline["id"], target["id"]) != json.loads(batch["automations"]):
            raise Conflict("As automacoes da etapa mudaram. Confira a movimentacao novamente.")
        if action == "transfer":
            transfer_checks(c, rows, destination)
            reason = "Oportunidade transferida para outro profissional ou funil"
            stop_automations(c, rows, now, reason)
            for row in rows:
                c.execute("UPDATE bot_events SET processed=1 WHERE lead_id=? AND processed=0", (row["id"],))
                # Legacy campaigns without frozen audiences cannot identify a more specific opportunity.
                c.execute("""UPDATE messages SET status='skipped',skip_reason=? WHERE contact_id=? AND status='queued'
                    AND campaign_id IN (SELECT ca.id FROM campaigns ca WHERE ca.doctor_id=?
                        AND NOT EXISTS(SELECT 1 FROM campaign_audiences a WHERE a.campaign_id=ca.id))""", (reason, row["contact_id"], row["doctor_id"]))
            source_doctor = store.row(c, "doctors", pipeline["doctor_id"])
            target_doctor = store.row(c, "doctors", destination["doctor_id"])
        for row in rows:
            if action == "move":
                c.execute("UPDATE leads SET stage_id=?,updated_at=?,revision=revision+1 WHERE id=?", (target["id"], now, row["id"]))
                store.stage_event(c, row, target, actor["nome"], now)
            elif action == "transfer":
                c.execute("""INSERT INTO lead_transfers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, batch["id"], row["id"], row["contact_id"], row["doctor_id"], row["pipeline_id"], row["stage_id"],
                     destination["doctor_id"], destination["id"], target["id"], str(actor["id"]), actor["nome"], now, json.dumps(row)))
                c.execute("""UPDATE leads SET doctor_id=?,pipeline_id=?,stage_id=?,updated_at=?,revision=revision+1,
                    source_name=CASE WHEN ?<>'' AND source_name='' THEN (SELECT name FROM contacts WHERE id=leads.contact_id) ELSE source_name END WHERE id=?""",
                    (destination["doctor_id"], destination["id"], target["id"], now, destination["course_key"], row["id"]))
                store.event(c, row["id"], actor["nome"], "transfer",
                    f"Transferido de {source_doctor['name']} / {pipeline['name']} para {target_doctor['name']} / {destination['name']} / {target['name']}. Conexao e conversas WhatsApp mantidas na origem.", now)
            else:
                c.execute("UPDATE leads SET deleted_at=?,deleted_by=?,updated_at=?,revision=revision+1 WHERE id=?",
                          (now if action == "delete" else 0, actor["nome"] if action == "delete" else "", now, row["id"]))
                store.event(c, row["id"], actor["nome"], action, "Enviado para a lixeira" if action == "delete" else "Restaurado da lixeira", now)
        if action == "delete":
            stop_automations(c, rows, now)
        result = {"action": action, "count": len(rows), "pipeline_id": destination["id"], "doctor_id": destination["doctor_id"]}
        c.execute("UPDATE lead_bulk_batches SET result=? WHERE id=?", (json.dumps(result), batch["id"]))
        return result


def trash(store, doctor, pipeline, page=1):
    from crm_store import integer
    page = integer(page, 1, 100000)
    with store.db() as c:
        store.owned(c, "pipelines", pipeline, doctor)
        rows = [dict(r) for r in c.execute("""SELECT l.id,l.title,l.revision,l.deleted_at,l.deleted_by,ct.name,s.name stage
            FROM leads l JOIN contacts ct ON ct.id=l.contact_id JOIN stages s ON s.id=l.stage_id
            WHERE l.doctor_id=? AND l.pipeline_id=? AND l.deleted_at>0 ORDER BY l.deleted_at DESC,l.id""", (doctor, pipeline))]
    return {"items": rows[(page-1)*50:page*50], "total": len(rows), "page": page, "has_more": page*50 < len(rows)}
