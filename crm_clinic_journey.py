"""Opt-in patient journey, audited closures and explicit, reversible source mapping."""
import hashlib
import json
import os
import sqlite3
import time
import unicodedata
import uuid

STAGES = [("new", "Novo contato", "open"), ("service", "Em atendimento", "open"),
          ("scheduled", "Avaliação agendada", "open"), ("consulted", "Consulta realizada", "open"),
          ("negotiation", "Negociação de Orçamento", "open"), ("patients", "Pacientes", "won"),
          ("closed", "Encerrado sem contratação", "lost"), ("noncommercial", "Não comercial", "lost")]
REASONS = {"value": ("Valor do investimento", "closed"),
           "payment": ("Condições de pagamento", "closed"),
           "withdrawal": ("Desistência / sem interesse", "closed"),
           "other_clinic": ("Escolheu outra clínica", "closed"),
           "schedule": ("Horários incompatíveis", "closed"),
           "location": ("Localização / deslocamento", "closed"),
           "unavailable": ("Serviço não oferecido", "closed"),
           "not_indicated": ("Sem indicação para o tratamento", "closed"),
           "no_response": ("Sem retorno após acompanhamento", "closed"),
           "other": ("Outro motivo", "closed"),
           "team": ("Equipe / contato interno", "noncommercial"),
           "supplier": ("Fornecedor / vendedor", "noncommercial"),
           "partner": ("Parceiro / prestador de serviço", "noncommercial")}


def normalized(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                           if not unicodedata.combining(c)).split())


def clinical(pipeline):
    return not (pipeline.get("course_key") or pipeline.get("institute_key") or
                "regencode" in normalized(pipeline["name"]).replace(" ", "").replace("-", "") or
                any(word in normalized(pipeline["name"]).split() for word in ("curso", "cursos", "mentoria", "instituto")))


def schema(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS clinic_journeys(pipeline_id TEXT PRIMARY KEY REFERENCES pipelines(id),
            stage_ids TEXT NOT NULL,created_at REAL NOT NULL,actor_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clinic_archived_stages(stage_id TEXT PRIMARY KEY REFERENCES stages(id),
            snapshot TEXT NOT NULL,target_id TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS clinic_card_state(lead_id TEXT PRIMARY KEY REFERENCES leads(id),
            disposition TEXT NOT NULL DEFAULT 'commercial',reason_id TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',needs_review INTEGER NOT NULL DEFAULT 0,
            legacy_stage TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS clinic_journey_events(id INTEGER PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),
            actor TEXT NOT NULL,created_at REAL NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clinic_journey_previews(id TEXT PRIMARY KEY,pipeline_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,digest TEXT NOT NULL,expires_at REAL NOT NULL,result TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS clinic_bulk_context(batch_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clinic_migration_rows(preview_id TEXT NOT NULL,lead_id TEXT NOT NULL,
            original TEXT NOT NULL,mapping TEXT NOT NULL,PRIMARY KEY(preview_id,lead_id));
    """)


def config(c, pipeline):
    row = c.execute("SELECT stage_ids FROM clinic_journeys WHERE pipeline_id=?", (pipeline,)).fetchone()
    return json.loads(row[0]) if row else None


def active_stages(c, pipeline):
    return [dict(r) for r in c.execute("""SELECT * FROM stages WHERE pipeline_id=? AND id NOT IN
        (SELECT stage_id FROM clinic_archived_stages) ORDER BY position""", (pipeline,))]


def catalog(c, pipeline):
    ids = config(c, pipeline)
    return {"stage_ids": ids, "reasons": [{"id": k, "name": v[0], "outcome": v[1]}
            for k, v in REASONS.items()]} if ids else None


def state(c, lead):
    row = c.execute("SELECT * FROM clinic_card_state WHERE lead_id=?", (lead,)).fetchone()
    return dict(row) if row else {"disposition": "commercial", "reason_id": "", "note": "", "needs_review": 0}


def attach(c, rows):
    for row in rows:
        row["journey_state"] = state(c, row["id"])


def validate_reason(payload, outcome):
    if not isinstance(payload, dict):
        raise ValueError("Selecione o motivo do encerramento.")
    reason, note = payload.get("reason_id", ""), payload.get("note", "")
    if not isinstance(reason, str) or reason not in REASONS or REASONS[reason][1] != outcome:
        raise ValueError("Selecione um motivo compatível com o encerramento.")
    if not isinstance(note, str) or len(note) > 1200:
        raise ValueError("Observação inválida (até 1200 caracteres).")
    if reason in {"other", "no_response", "not_indicated"} and not note.strip():
        raise ValueError("Registre a justificativa deste encerramento.")
    if reason == "no_response" and payload.get("confirm_attempts") is not True:
        raise ValueError("Confirme as tentativas e a ausência de retorno combinado.")
    if reason == "not_indicated" and payload.get("confirm_professional") is not True:
        raise ValueError("Confirme que a decisão foi validada pelo profissional.")
    return {"disposition": "noncommercial" if outcome == "noncommercial" else "commercial",
            "reason_id": reason, "note": note.strip(), "needs_review": 0}


def prepare(c, lead, target, payload=None):
    ids = config(c, target["pipeline_id"])
    if not ids:
        return None
    if target["id"] not in ids.values():
        raise ValueError("Esta etapa foi arquivada. Atualize o funil.")
    if lead and lead["stage_id"] == target["id"] and payload is None:
        return None
    outcome = next((key for key in ("closed", "noncommercial") if ids[key] == target["id"]), "")
    if not outcome:
        return {"disposition": "commercial", "reason_id": "", "note": "", "needs_review": 0}
    return validate_reason(payload, outcome)


def record(c, store, lead, result, actor, now):
    if result is None:
        return
    before = state(c, lead["id"])
    if all(before.get(k) == v for k, v in result.items()):
        return
    c.execute("""INSERT INTO clinic_card_state(lead_id,disposition,reason_id,note,needs_review,updated_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET disposition=excluded.disposition,
        reason_id=excluded.reason_id,note=excluded.note,needs_review=excluded.needs_review,updated_at=excluded.updated_at""",
        (lead["id"], result["disposition"], result["reason_id"], result["note"], result["needs_review"], now))
    c.execute("INSERT INTO clinic_journey_events(lead_id,actor,created_at,before_json,after_json) VALUES(?,?,?,?,?)",
              (lead["id"], actor, now, json.dumps(before), json.dumps(result)))
    label = REASONS.get(result["reason_id"], ("Reabertura / jornada comercial",))[0]
    store.event(c, lead["id"], actor, "classification", label + (": " + result["note"] if result["note"] else ""), now)
    if result["disposition"] == "noncommercial":
        from crm_bots import BotStore
        BotStore.cancel(c, "lead_id=?", (lead["id"],), "Contato não comercial", now)


def mapping(stage):
    name = normalized(stage["name"])
    if any(word in name for word in ("fornecedor", "vendedor")):
        return "noncommercial", "supplier", False
    if any(word in name for word in ("equipe", "colaborador", "funcionario")):
        return "noncommercial", "team", False
    if "nao comercial" in name:
        return "noncommercial", "partner", True
    if stage["kind"] == "won" or name in {"pacientes", "paciente", "clientes", "ganho", "contratado"}:
        return "patients", "", False
    if stage["kind"] == "lost":
        return "closed", "legacy_unclassified", True
    if name in {"sem interesse", "desistiu", "recusou"}:
        return "closed", "withdrawal", False
    if any(word in name for word in ("consulta realizada", "avaliacao realizada")):
        return "consulted", "", False
    if "agendad" in name and not any(word in name for word in ("nao", "desmarc", "reagend")):
        return "scheduled", "", False
    if any(word in name for word in ("negociacao", "proposta enviada", "orcamento enviado", "orcamento apresentado", "em decisao")):
        return "negotiation", "", False
    if name in {"novo contato", "novo lead", "entrada", "novos leads", "leads recebidos", "virgem"}:
        return "new", "", False
    if name in {"em atendimento", "em conversa", "primeiro contato feito", "contato feito"}:
        return "service", "", False
    return "service", "", True


def plan(store, c, doctor, pipeline):
    owner = store.owned(c, "pipelines", pipeline, doctor)
    if not clinical(owner):
        raise ValueError("Este modelo é exclusivo da clínica. Cursos e RegenCode ficam inalterados.")
    if config(c, pipeline):
        raise ValueError("Este funil já utiliza o modelo por paciente.")
    stages = active_stages(c, pipeline)
    source = {r["id"]: r for r in stages}
    rows = [dict(r) for r in c.execute("""SELECT l.*,ct.experts_uuid,ct.experts_status FROM leads l
        JOIN contacts ct ON ct.id=l.contact_id WHERE l.pipeline_id=? ORDER BY l.id""", (pipeline,))]
    if len(rows) > 30000:
        raise ValueError("Este volume exige migração assistida em manutenção.")
    store.attach_customer_values(c, rows, store.row(c, "doctors", doctor)["name"])
    store.attach_appointments(rows, store.row(c, "doctors", doctor)["name"])
    result, counts = [], {key: 0 for key, _, _ in STAGES}
    for row in rows:
        stage = source.get(row["stage_id"])
        if not stage:
            raise ValueError("Há um cartão com etapa inconsistente; revise antes de migrar.")
        key, reason, review = mapping(stage)
        history = row.get("customer_history", {})
        value = row.get("customer_value", {})
        evidence = "Etapa anterior: " + stage["name"]
        if key != "noncommercial" and value.get("state") == "ready" and value.get("sales_count", 0) > 0:
            key, reason, review = "patients", "", False
            evidence += "; venda válida vinculada ao profissional"
        elif key in {"new", "service"} and history.get("quotes_state") == "ready" and history.get("pending_quotes_count", 0) > 0:
            key = "negotiation"
            evidence += "; orçamento pendente vinculado ao profissional"
        elif key in {"new", "service", "scheduled"}:
            appointment = row.get("appointment", {})
            item = appointment.get("item") or {}
            if appointment.get("state") == "ready" and not any(appointment.get("warnings", {}).values()):
                evaluation = any(word in normalized(item.get("procedure", "")) for word in ("consulta", "avaliacao"))
                if item.get("attendance") == "completed" and evaluation:
                    key, review = "consulted", False
                    evidence += "; atendimento realizado na agenda do profissional"
                elif evaluation and item.get("phase") in {"upcoming", "ongoing"} and item.get("status") in {"confirmed", "arrived", "waiting", "in_progress"}:
                    key, review = "scheduled", False
                    evidence += "; agendamento confirmado vinculado ao profissional"
        result.append({"id": row["id"], "revision": row["revision"], "stage_id": row["stage_id"],
                       "target": key, "reason_id": reason, "review": review, "evidence": evidence})
        if not row["deleted_at"]:
            counts[key] += 1
    digest = hashlib.sha256(json.dumps([owner, stages, result], sort_keys=True).encode()).hexdigest()
    return owner, stages, result, counts, digest


def preview(store, doctor, pipeline, actor):
    with store.db() as c:
        owner, stages, rows, counts, digest = plan(store, c, doctor, pipeline)
        ident = uuid.uuid4().hex
        c.execute("INSERT INTO clinic_journey_previews VALUES(?,?,?,?,?,?)",
                  (ident, pipeline, str(actor["id"]), digest, time.time()+900, ""))
        return {"id": ident, "pipeline": owner["name"], "count": sum(counts.values()),
                "review": sum(r["review"] for r in rows), "stages": [{"key": k, "name": n, "count": counts[k]} for k, n, _ in STAGES],
                "source_stages": [{"name": s["name"], "target": mapping(s)[0]} for s in stages]}


def backup(store):
    folder = store.path.parent / "backups"
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / ("patient-journey-" + uuid.uuid4().hex + ".sqlite3")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with sqlite3.connect(store.path.as_uri()+"?mode=ro", uri=True) as source, sqlite3.connect(path) as target:
        source.backup(target)
    return path.name


def apply(store, doctor, payload, actor):
    from crm_store import Conflict
    if payload.get("confirm") is not True:
        raise ValueError("Confirme a migração do funil da clínica.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        receipt = c.execute("SELECT * FROM clinic_journey_previews WHERE id=?", (payload.get("id"),)).fetchone()
        if not receipt or receipt["actor_id"] != str(actor["id"]):
            raise LookupError("Prévia indisponível.")
        store.owned(c, "pipelines", receipt["pipeline_id"], doctor)
        if receipt["result"]:
            return json.loads(receipt["result"])
        if receipt["expires_at"] < time.time():
            raise Conflict("A prévia expirou. Gere outra antes de aplicar.")
        owner, stages, rows, counts, digest = plan(store, c, doctor, receipt["pipeline_id"])
        if digest != receipt["digest"]:
            raise Conflict("O funil ou os dados vinculados mudaram. Confira uma nova prévia.")
        backup_name = backup(store)
        now = time.time()
        ids = {k: uuid.uuid4().hex for k, _, _ in STAGES}
        for key, name, kind in STAGES:
            c.execute("INSERT INTO stages VALUES(?,?,?,?,?)", (ids[key], owner["id"], name, list(ids).index(key), kind))
        c.execute("INSERT INTO clinic_journeys VALUES(?,?,?,?)", (owner["id"], json.dumps(ids), now, str(actor["id"])))
        for stage in stages:
            target = ids[mapping(stage)[0]]
            c.execute("INSERT INTO clinic_archived_stages VALUES(?,?,?,?)", (stage["id"], json.dumps(stage), target, now))
        from crm_patient_opportunities import seed_legacy
        from crm_bots import BotStore
        for item in rows:
            lead = dict(c.execute("SELECT * FROM leads WHERE id=?", (item["id"],)).fetchone())
            c.execute("INSERT INTO clinic_migration_rows VALUES(?,?,?,?)", (receipt["id"], lead["id"], json.dumps(lead), json.dumps(item)))
            c.execute("UPDATE leads SET stage_id=?,revision=revision+1 WHERE id=?", (ids[item["target"]], lead["id"]))
            result = {"disposition": "noncommercial" if item["target"] == "noncommercial" else "commercial",
                      "reason_id": item["reason_id"], "note": item["evidence"], "needs_review": int(item["review"])}
            record(c, store, lead, result, actor["nome"], now)
            c.execute("UPDATE clinic_card_state SET legacy_stage=? WHERE lead_id=?", (lead["stage_id"], lead["id"]))
            store.event(c, lead["id"], actor["nome"], "journey_migration", item["evidence"] + " → " + next(n for k,n,_ in STAGES if k==item["target"]), now)
            if item["target"] != "noncommercial":
                seed_legacy(c, lead, item, actor, now)
            BotStore.cancel(c, "lead_id=?", (lead["id"],), "Revisão do modelo de funil", now)
            c.execute("UPDATE bot_events SET processed=1 WHERE lead_id=? AND processed=0", (lead["id"],))
        c.execute("UPDATE crm_bots SET status='paused',revision=revision+1 WHERE pipeline_id=? AND status='active'", (owner["id"],))
        c.execute("UPDATE channels SET inbound_stage_id=?,config_revision=config_revision+1 WHERE inbound_pipeline_id=?", (ids["new"], owner["id"]))
        c.execute("UPDATE pipelines SET revision=revision+1 WHERE id=?", (owner["id"],))
        result = {"pipeline_id": owner["id"], "count": sum(counts.values()), "review": sum(r["review"] for r in rows),
                  "backup": backup_name, "stage_ids": ids}
        c.execute("UPDATE clinic_journey_previews SET result=? WHERE id=?", (json.dumps(result), receipt["id"]))
        if c.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Integridade inconsistente; migração revertida.")
        return result
