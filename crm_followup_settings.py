"""Editable follow-up policies. Saving or previewing never enrolls contacts or sends."""
from datetime import datetime, timedelta
import json
import re
import time
from zoneinfo import ZoneInfo

from crm_clinic_journey import active_stages, clinical
from crm_store import Conflict

MAX_STEPS = 20
TZ = ZoneInfo("America/Sao_Paulo")


def schema(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS followup_policies(
            pipeline_id TEXT PRIMARY KEY REFERENCES pipelines(id),revision INTEGER NOT NULL,
            enabled INTEGER NOT NULL,config TEXT NOT NULL,activated_at REAL NOT NULL,
            updated_by TEXT NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS followup_policy_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,pipeline_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,created_at REAL NOT NULL,snapshot TEXT NOT NULL);
    """)


def defaults():
    messages = [
        "Oi, {nome}! Ficou alguma dúvida sobre o que conversamos? Estamos por aqui para te ajudar.",
        "Olá, {nome}! Você gostaria de retomar nossa conversa agora ou prefere que a gente entre em contato em outra data?",
        "Oi, {nome}! Se ainda fizer sentido para você, nossa equipe está à disposição para ajudar com o próximo passo.",
        "Oi, {nome}! Vamos pausar o acompanhamento por aqui. Quando quiser continuar, é só nos chamar. Estamos à disposição!",
    ]
    return {"mode": "send", "stage_ids": [], "weekdays": [0, 1, 2, 3, 4],
            "from": "09:00", "to": "18:00", "timezone": "America/Sao_Paulo",
            "spacing_seconds": 180, "max_age_days": 180, "assignee_id": "",
            "steps": [{"minutes": minutes, "title": f"Follow-up {i+1}", "body": body}
                      for i, (minutes, body) in enumerate(zip((120, 1320, 2880, 5760), messages))]}


def number(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}: informe um inteiro de {low} a {high}.")
    return value


def clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Horário inválido. Use HH:MM.")
    hour, minute = map(int, value.split(":"))
    return hour*60+minute


def owner(store, c, doctor, pipeline):
    if not isinstance(pipeline, str) or not pipeline:
        raise ValueError("Selecione um funil.")
    item = store.owned(c, "pipelines", pipeline, doctor)
    if not clinical(item):
        raise ValueError("Esta régua é exclusiva dos funis clínicos. Cursos permanecem inalterados.")
    return item


def clean(c, pipeline, raw):
    if not isinstance(raw, dict) or raw.get("mode") != "send":
        raise ValueError("Configuração de follow-up inválida.")
    days = raw.get("weekdays")
    if not isinstance(days, list) or not days or any(type(d) is not int or d not in range(7) for d in days) or len(set(days)) != len(days):
        raise ValueError("Selecione os dias de funcionamento.")
    if raw.get("timezone") != "America/Sao_Paulo" or clock(raw.get("from")) >= clock(raw.get("to")):
        raise ValueError("O início precisa ser anterior ao fim, no horário de Brasília.")
    available = {s["id"] for s in active_stages(c, pipeline) if s["kind"] == "open"}
    stages = raw.get("stage_ids")
    if not isinstance(stages, list) or any(not isinstance(s, str) or s not in available for s in stages) or len(set(stages)) != len(stages):
        raise ValueError("Selecione etapas abertas e atuais deste funil.")
    spacing = number(raw.get("spacing_seconds"), 60, 86400, "Intervalo entre envios")
    if spacing % 60:
        raise ValueError("O intervalo entre envios deve usar minutos inteiros.")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError("Defina de 1 a 20 follow-ups.")
    result = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError("Tentativa inválida.")
        minutes = number(step.get("minutes"), 15, 43200, f"Intervalo do follow-up {i+1}")
        body = step.get("body")
        if not isinstance(body, str) or not body.strip() or len(body) > 2000:
            raise ValueError(f"Preencha a mensagem do follow-up {i+1} (até 2000 caracteres).")
        if re.search(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f]", body):
            raise ValueError("Não use caracteres invisíveis ou de controle na mensagem.")
        if any(key not in {"nome", "clinica"} for key in re.findall(r"\{([^{}]+)\}", body)):
            raise ValueError("Campos disponíveis na mensagem: {nome} e {clinica}.")
        result.append({"minutes": minutes, "title": f"Follow-up {i+1}", "body": body.strip()})
    if sum(s["minutes"] for s in result) > 180*1440:
        raise ValueError("A régua não pode ultrapassar 180 dias.")
    return {"mode": "send", "stage_ids": stages, "weekdays": sorted(days),
            "from": raw["from"], "to": raw["to"], "timezone": "America/Sao_Paulo",
            "spacing_seconds": spacing, "max_age_days": 180, "assignee_id": "", "steps": result}


def next_window(config, stamp):
    local = datetime.fromtimestamp(stamp, TZ)
    start, end = clock(config["from"]), clock(config["to"])
    for offset in range(8):
        day = local + timedelta(days=offset)
        if day.weekday() not in config["weekdays"]:
            continue
        opening = day.replace(hour=start//60, minute=start%60, second=0, microsecond=0)
        closing = day.replace(hour=end//60, minute=end%60, second=0, microsecond=0)
        candidate = max(local, opening) if offset == 0 else opening
        if candidate < closing:
            return candidate.timestamp()
    raise ValueError("Não foi possível calcular o próximo expediente.")


def slots(config, stamp, count=3, last_send_at=0):
    """Pure preview, not a reservation or a substitute for the shared worker lock."""
    gap = config["spacing_seconds"]
    cursor = max(stamp, last_send_at+gap) if last_send_at else stamp
    result = []
    for _ in range(count):
        due = next_window(config, cursor)
        result.append(due)
        cursor = due+gap
    return result


def snapshot(store, doctor, pipeline):
    with store.db() as c:
        item = owner(store, c, doctor, pipeline)
        row = c.execute("SELECT * FROM followup_policies WHERE pipeline_id=?", (pipeline,)).fetchone()
        config = json.loads(row["config"]) if row else defaults()
        config.setdefault("spacing_seconds", 180)
        stages = [s for s in active_stages(c, pipeline) if s["kind"] == "open"]
        if not row:
            from crm_clinic_journey import config as journey_config
            ids = journey_config(c, pipeline)
            config["stage_ids"] = [ids[k] for k in ("service", "negotiation")] if ids else [s["id"] for s in stages if s["name"] in {"Em conversa", "Proposta enviada"}]
        metrics = {r[0]: r[1] for r in c.execute("SELECT status,count(*) FROM crm_followup_cycles WHERE pipeline_id=? GROUP BY status",(pipeline,))}
        return {"pipeline_id": pipeline, "pipeline_name": item["name"], "revision": row["revision"] if row else 0,
                "config": config, "enabled": bool(row["enabled"]) if row else False,
                "automatic_available": True, "max_steps": MAX_STEPS, "metrics": metrics,
                "updated_at": row["updated_at"] if row else 0,
                "stages": [{"id": s["id"], "name": s["name"]} for s in stages],
                "preview": slots(config, time.time())}


def save(store, doctor, payload, user):
    if not user.get("is_master"):
        raise PermissionError("Somente o Master pode editar a régua de follow-up.")
    if payload.get("enabled") not in (None, False) or payload.get("activate") not in (None, False):
        raise ValueError("A ativação de envios ainda não está disponível.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        pipeline = payload.get("pipeline_id")
        owner(store, c, doctor, pipeline)
        old = c.execute("SELECT * FROM followup_policies WHERE pipeline_id=?", (pipeline,)).fetchone()
        revision = number(payload.get("revision"), 0, 2147483647, "Revisão")
        if revision != (old["revision"] if old else 0) or old and old["enabled"]:
            raise Conflict("A régua mudou. Atualize a página antes de salvar.")
        config, now = clean(c, pipeline, payload.get("config")), time.time()
        c.execute("""INSERT INTO followup_policies VALUES(?,?,0,?,0,?,?)
            ON CONFLICT(pipeline_id) DO UPDATE SET revision=excluded.revision,config=excluded.config,
            enabled=0,activated_at=0,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                  (pipeline, revision+1, json.dumps(config), str(user["id"]), now))
        c.execute("INSERT INTO followup_policy_events(pipeline_id,actor_id,created_at,snapshot) VALUES(?,?,?,?)",
                  (pipeline, str(user["id"]), now, json.dumps({"revision": revision+1, "enabled": False, "config": config})))
    return snapshot(store, doctor, pipeline)


def preview(store, doctor, payload):
    with store.db() as c:
        pipeline = payload.get("pipeline_id")
        owner(store, c, doctor, pipeline)
        config = clean(c, pipeline, payload.get("config"))
    now = time.time()
    if payload.get("at_local"):
        try:
            local = datetime.fromisoformat(payload["at_local"])
            if local.tzinfo is not None:
                raise ValueError()
            now = local.replace(tzinfo=TZ).timestamp()
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Data da simulação inválida.") from None
    return {"slots": slots(config, now), "scheduled": 0, "enabled": False}


def toggle(store, doctor, payload, user):
    if not user.get("is_master"):
        raise PermissionError("Somente o Master pode ligar ou desligar a régua.")
    enabled=payload.get("enabled")
    if type(enabled) is not bool or enabled and payload.get("confirm") is not True:
        raise ValueError("Confirme a ativação para novos atendimentos.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        pipeline=payload.get("pipeline_id");owner(store,c,doctor,pipeline)
        old=c.execute("SELECT * FROM followup_policies WHERE pipeline_id=?",(pipeline,)).fetchone()
        if not old or type(payload.get("revision")) is not int or payload["revision"]!=old["revision"]:
            raise Conflict("Salve e atualize a régua antes de alterar a ativação.")
        config=clean(c,pipeline,json.loads(old["config"]))
        if enabled and not config["stage_ids"]:
            raise ValueError("Selecione ao menos uma etapa para ativar a régua.")
        from crm_followup_runtime import stop_pipeline
        now=time.time();stop_pipeline(c,pipeline,"Régua ligada ou desligada; tentativas antigas não são retomadas",now)
        c.execute("UPDATE followup_policies SET enabled=?,revision=revision+1,activated_at=?,updated_by=?,updated_at=? WHERE pipeline_id=?",
            (int(enabled),now if enabled else 0,str(user["id"]),now,pipeline))
        c.execute("INSERT INTO followup_policy_events(pipeline_id,actor_id,created_at,snapshot) VALUES(?,?,?,?)",
            (pipeline,str(user["id"]),now,json.dumps({"enabled":enabled,"revision":old["revision"]+1})))
    return snapshot(store,doctor,pipeline)
