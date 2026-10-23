"""Native, explicitly requested follow-up on the shared WhatsApp queue."""
import json
import logging
import re
import time
import unicodedata
import uuid
from datetime import datetime

from crm_followup_settings import TZ, next_window


def schema(c):
    for name, definition in (("followup_cycle_id", "TEXT NOT NULL DEFAULT ''"), ("followup_attempt", "INTEGER NOT NULL DEFAULT 0")):
        if name not in {r[1] for r in c.execute("PRAGMA table_info(messages)")}:
            c.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")
    for name, definition in (("followup_requested", "INTEGER NOT NULL DEFAULT 0"), ("followup_lead_id", "TEXT NOT NULL DEFAULT ''")):
        if name not in {r[1] for r in c.execute("PRAGMA table_info(reply_requests)")}:
            c.execute(f"ALTER TABLE reply_requests ADD COLUMN {name} {definition}")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS crm_followup_cycles(
            id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),contact_id TEXT NOT NULL,
            doctor_id TEXT NOT NULL,pipeline_id TEXT NOT NULL,stage_id TEXT NOT NULL,channel_id TEXT NOT NULL,
            phone TEXT NOT NULL,phone_revision INTEGER NOT NULL,channel_revision INTEGER NOT NULL,
            policy_revision INTEGER NOT NULL,actor_id TEXT NOT NULL,activator_id TEXT NOT NULL,
            anchor_id TEXT NOT NULL,inbound_seq INTEGER NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,
            config TEXT NOT NULL,due_at REAL NOT NULL,message_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'waiting',reason TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,updated_at REAL NOT NULL,checked_at REAL NOT NULL DEFAULT 0,
            UNIQUE(contact_id,inbound_seq));
        CREATE UNIQUE INDEX IF NOT EXISTS crm_followup_active_contact ON crm_followup_cycles(contact_id) WHERE status='waiting';
        CREATE UNIQUE INDEX IF NOT EXISTS crm_followup_message_attempt ON messages(followup_cycle_id,followup_attempt) WHERE followup_cycle_id<>'';
        CREATE TABLE IF NOT EXISTS crm_followup_events(id INTEGER PRIMARY KEY,cycle_id TEXT NOT NULL,
            kind TEXT NOT NULL,reason TEXT NOT NULL,created_at REAL NOT NULL);
    """)


def inbound(c, contact):
    return c.execute("SELECT coalesce(max(rowid),0) FROM messages WHERE contact_id=? AND direction='in'",(contact,)).fetchone()[0]


def log(c, cycle, kind, reason, now):
    c.execute("INSERT INTO crm_followup_events(cycle_id,kind,reason,created_at) VALUES(?,?,?,?)",(cycle,kind,reason,now))


def stop(c, cycle, reason, now, status="cancelled"):
    c.execute("UPDATE crm_followup_cycles SET status=?,reason=?,updated_at=? WHERE id=?",(status,reason,now,cycle["id"]))
    c.execute("UPDATE messages SET status='skipped',skip_reason=? WHERE followup_cycle_id=? AND status='queued'",(reason,cycle["id"]))
    log(c,cycle["id"],status,reason,now)


def stop_contact(c, contact, reason, now):
    for cycle in c.execute("SELECT * FROM crm_followup_cycles WHERE contact_id=? AND status='waiting'",(contact,)).fetchall():
        stop(c,cycle,reason,now)


def stop_pipeline(c, pipeline, reason, now):
    for cycle in c.execute("SELECT * FROM crm_followup_cycles WHERE pipeline_id=? AND status='waiting'",(pipeline,)).fetchall():
        stop(c,cycle,reason,now)


def requested(c, store, message, payload, doctor):
    if payload.get("followup_requested") is not True:
        return
    lead_id = payload.get("followup_lead_id")
    if not isinstance(lead_id,str):
        raise ValueError("Paciente inválido para acompanhamento.")
    lead=store.owned(c,"leads",lead_id,doctor)
    target=c.execute("SELECT contact_id FROM messages WHERE id=?",(message,)).fetchone()
    if not target or target[0]!=lead["contact_id"]:
        raise ValueError("O acompanhamento pertence a outro contato.")
    c.execute("UPDATE reply_requests SET followup_requested=1,followup_lead_id=? WHERE message_id=?",(lead_id,message))


def body_for(c, cycle):
    contact=c.execute("SELECT name FROM contacts WHERE id=?",(cycle["contact_id"],)).fetchone()
    name=(contact[0].strip().split() or [""])[0]
    body=json.loads(cycle["config"])["steps"][cycle["attempt"]-1]["body"]
    return body.replace("{nome}",name).replace("{clinica}","Vielle Clinic")


def guard(c, store, cycle, now, *, own_message=""):
    lead=c.execute("""SELECT l.*,ct.opted_out,ct.merged_into,ct.experts_status,
        coalesce(ps.phone,ct.phone) phone,coalesce(ps.revision,1) phone_revision,s.kind,
        coalesce(cs.disposition,'commercial') disposition,p.course_key,p.institute_key
        FROM leads l JOIN contacts ct ON ct.id=l.contact_id JOIN stages s ON s.id=l.stage_id
        JOIN pipelines p ON p.id=l.pipeline_id LEFT JOIN contact_phone_settings ps ON ps.contact_id=ct.id
        LEFT JOIN clinic_card_state cs ON cs.lead_id=l.id WHERE l.id=?""",(cycle["lead_id"],)).fetchone()
    if not lead or lead["deleted_at"] or lead["opted_out"] or lead["merged_into"]:
        return "cancelled","Contato indisponível ou descadastrado"
    if (lead["contact_id"],lead["doctor_id"],lead["pipeline_id"],lead["stage_id"])!=(cycle["contact_id"],cycle["doctor_id"],cycle["pipeline_id"],cycle["stage_id"]) or lead["kind"]!="open" or lead["disposition"]!="commercial" or lead["course_key"] or lead["institute_key"]:
        return "cancelled","Etapa ou contexto comercial alterado"
    policy=c.execute("SELECT * FROM followup_policies WHERE pipeline_id=?",(cycle["pipeline_id"],)).fetchone()
    if not policy or not policy["enabled"] or policy["revision"]!=cycle["policy_revision"]:
        return "cancelled","Régua alterada ou desligada"
    config=json.loads(cycle["config"])
    if lead["stage_id"] not in config["stage_ids"]:
        return "cancelled","Etapa fora da régua"
    if not lead["phone"] or (lead["phone"],lead["phone_revision"])!=(cycle["phone"],cycle["phone_revision"]):
        return "cancelled","Telefone alterado; tentativa não redirecionada"
    if lead["experts_status"]=="review":
        return "paused","Identidade precisa de conferência"
    if not lead["consent_at"] or not lead["consent_source"].strip():
        return "paused","Autorização de acompanhamento não registrada na ficha"
    if c.execute("""SELECT 1 FROM leads l JOIN stages s ON s.id=l.stage_id
        WHERE l.contact_id=? AND l.id<>? AND l.deleted_at=0 AND s.kind='open' LIMIT 1""",(cycle["contact_id"],lead["id"])).fetchone():
        return "paused","Mais de um contexto comercial aberto; conferir com a equipe"
    ch=c.execute("SELECT * FROM channels WHERE id=?",(cycle["channel_id"],)).fetchone()
    if not ch or ch["archived_at"] or ch["doctor_id"]!=cycle["doctor_id"] or ch["config_revision"]!=cycle["channel_revision"]:
        return "cancelled","Conexão removida, transferida ou alterada"
    if ch["state"]!="open":
        return "paused","Número desconectado; sem retomada automática"
    if inbound(c,cycle["contact_id"])!=cycle["inbound_seq"]:
        return "cancelled","Cliente respondeu"
    if store.conversation_status(c,cycle["channel_id"],cycle["contact_id"])["resolution"]=="closed":
        return "cancelled","Conversa encerrada"
    if now-cycle["created_at"]>config["max_age_days"]*86400:
        return "paused","Prazo máximo alcançado"
    if c.execute("SELECT 1 FROM messages WHERE contact_id=? AND status='unknown' LIMIT 1",(cycle["contact_id"],)).fetchone():
        return "paused","Envio sem confirmação; não reenviar"
    if c.execute("SELECT 1 FROM crm_reminders WHERE lead_id=? AND status='pending' LIMIT 1",(lead["id"],)).fetchone() or lead["next_action"] and lead["next_action"]>=datetime.fromtimestamp(now,TZ).date().isoformat():
        return "paused","Retorno ou tarefa já combinado na ficha"
    if c.execute("SELECT 1 FROM bot_runs r JOIN leads l ON l.id=r.lead_id WHERE l.contact_id=? AND r.status NOT IN ('done','failed','cancelled') LIMIT 1",(cycle["contact_id"],)).fetchone():
        return "paused","Outra automação deste contato está em andamento"
    anchor=c.execute("SELECT rowid,* FROM messages WHERE id=?",(cycle["anchor_id"],)).fetchone()
    if not anchor or anchor["origin"]!="crm" or anchor["status"]!="sent" or not anchor["sender_user_id"]:
        return "paused","Resposta humana sem confirmação confiável"
    if c.execute("SELECT 1 FROM messages WHERE contact_id=? AND direction='out' AND rowid>? AND origin<>'followup' LIMIT 1",(cycle["contact_id"],anchor["rowid"])).fetchone():
        return "cancelled","Novo atendimento após o início da régua"
    if c.execute("SELECT 1 FROM messages WHERE contact_id=? AND id<>? AND status IN ('queued','sending') AND origin<>'followup' LIMIT 1",(cycle["contact_id"],own_message)).fetchone():
        return "defer","Outro envio aguardando processamento"
    rows=c.execute("SELECT body,media_id FROM messages WHERE contact_id=? AND rowid>=? AND origin<>'followup' ORDER BY rowid LIMIT 101",(cycle["contact_id"],cycle["inbound_seq"])).fetchall()
    context=" ".join(r[0] for r in rows)
    normalized=unicodedata.normalize("NFKD",context).encode("ascii","ignore").decode().casefold()
    if len(rows)>100 or any(r[1] for r in rows) or re.search(r"\b(dor|dores|febre|sangramento|necrose|alergia|intercorrencia|complicacao|reclamacao|insatisfeit\w*|pos[- ](?:procedimento|operatorio)|agendad[ao]|consulta confirmada|sem interesse|nao quero|me chame|me ligue|entre em contato depois)\b",normalized) or re.search(r"\[(audio|imagem|video|documento)\]",normalized):
        return "paused","Contexto clínico, mídia ou retorno solicitado exige equipe"
    if store.appointment_source and lead["experts_status"]=="matched":
        item=dict(lead);doctor=c.execute("SELECT name FROM doctors WHERE id=?",(cycle["doctor_id"],)).fetchone()[0]
        try:store.attach_appointments([item],doctor)
        except Exception:return "paused","Agenda indisponível; conferir antes de acompanhar"
        agenda=item.get("appointment",{})
        if agenda.get("state")!="ready" or agenda.get("kind") in {"next","ongoing"}:
            return "paused","Agenda ou continuidade de tratamento requer equipe"
    return None


def accepted(c, store, message, now):
    row=c.execute("""SELECT m.*,r.followup_requested,r.followup_lead_id FROM messages m
        JOIN reply_requests r ON r.message_id=m.id WHERE m.id=?""",(message,)).fetchone()
    if not row or row["origin"]!="crm" or row["status"]!="sent":return
    if not row["followup_requested"] or row["media_id"]:
        stop_contact(c,row["contact_id"],"Atendimento humano retomado",now);return
    lead=c.execute("SELECT l.*,coalesce(ps.phone,ct.phone) phone,coalesce(ps.revision,1) phone_revision FROM leads l JOIN contacts ct ON ct.id=l.contact_id LEFT JOIN contact_phone_settings ps ON ps.contact_id=ct.id WHERE l.id=?",(row["followup_lead_id"],)).fetchone()
    if not lead or lead["contact_id"]!=row["contact_id"]:return
    rule=c.execute("SELECT * FROM followup_policies WHERE pipeline_id=? AND enabled=1",(lead["pipeline_id"],)).fetchone()
    if not rule or row["created_at"]<rule["activated_at"]:return
    config=json.loads(rule["config"])
    if lead["stage_id"] not in config["stage_ids"]:return
    turn=inbound(c,lead["contact_id"])
    existing=c.execute("SELECT * FROM crm_followup_cycles WHERE contact_id=? AND inbound_seq=?",(lead["contact_id"],turn)).fetchone()
    if existing:
        if existing["status"]!="waiting" or existing["anchor_id"]==message:return
        if (existing["lead_id"],existing["channel_id"],existing["policy_revision"])!=(lead["id"],row["channel_id"],rule["revision"]):
            stop(c,existing,"Outro contexto solicitou acompanhamento",now,"paused");return
        if existing["message_id"]:
            stop(c,existing,"Nova resposta com tentativa já reservada; conferir contexto",now,"paused");return
        due=next_window(config,now+config["steps"][existing["attempt"]]["minutes"]*60)
        c.execute("UPDATE crm_followup_cycles SET anchor_id=?,due_at=?,updated_at=? WHERE id=?",(message,due,now,existing["id"]))
        return
    ch=c.execute("SELECT * FROM channels WHERE id=?",(row["channel_id"],)).fetchone()
    if not ch or row["recipient_phone"]!=lead["phone"]:return
    stop_contact(c,lead["contact_id"],"Novo turno de atendimento",now)
    ident=uuid.uuid4().hex;due=next_window(config,now+config["steps"][0]["minutes"]*60)
    values=(ident,lead["id"],lead["contact_id"],lead["doctor_id"],lead["pipeline_id"],lead["stage_id"],ch["id"],lead["phone"],lead["phone_revision"],ch["config_revision"],rule["revision"],row["sender_user_id"],rule["updated_by"],message,turn,rule["config"],due,now,now)
    c.execute("""INSERT INTO crm_followup_cycles(id,lead_id,contact_id,doctor_id,pipeline_id,stage_id,channel_id,phone,phone_revision,channel_revision,policy_revision,actor_id,activator_id,anchor_id,inbound_seq,config,due_at,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",values)
    cycle=c.execute("SELECT * FROM crm_followup_cycles WHERE id=?",(ident,)).fetchone()
    reason=guard(c,store,cycle,now)
    if reason and reason[0]!="defer":stop(c,cycle,reason[1],now,reason[0])
    else:log(c,ident,"started","Acompanhamento solicitado pela equipe; somente novos eventos",now)


def permitted(store, auth):
    from crm_reminders import assignee_allowed
    def check(cycle):
        try:
            master=auth.get_public_user(cycle["activator_id"])
            return bool(master and master.get("is_master") and master.get("status")=="active" and assignee_allowed(store,auth,cycle["actor_id"],cycle))
        except Exception:
            logging.warning("CRM follow-up permission unavailable; attempt denied")
            return False
    return check


def settle(c, store, message, now):
    cycle=c.execute("SELECT * FROM crm_followup_cycles WHERE id=? AND message_id=?",(message["followup_cycle_id"],message["id"])).fetchone()
    if not cycle or cycle["status"]!="waiting":return
    steps=json.loads(cycle["config"])["steps"]
    if cycle["attempt"]>=len(steps):
        stop(c,cycle,"Tentativas concluídas; etapa do paciente preservada",now,"completed");return
    config=json.loads(cycle["config"])
    due=next_window(config,now+steps[cycle["attempt"]]["minutes"]*60)
    c.execute("UPDATE crm_followup_cycles SET message_id='',due_at=?,updated_at=? WHERE id=?",(due,now,cycle["id"]))
    log(c,cycle["id"],"accepted","Aceita pelo conector; entrega não confirmada",now)


def advance(store, now=None):
    now=time.time() if now is None else now
    allowed=getattr(store,"followup_permissions",None)
    if not callable(allowed):return False
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        cycles=c.execute("SELECT * FROM crm_followup_cycles WHERE status='waiting' ORDER BY checked_at,created_at LIMIT 50").fetchall()
        for cycle in cycles:
            c.execute("UPDATE crm_followup_cycles SET checked_at=? WHERE id=?",(now,cycle["id"]))
            if not allowed(dict(cycle)):
                stop(c,cycle,"Gestor ou responsável sem acesso atual",now,"paused");continue
            reason=guard(c,store,cycle,now,own_message=cycle["message_id"])
            if reason:
                if reason[0]!="defer":stop(c,cycle,reason[1],now,reason[0])
                continue
            if cycle["message_id"]:
                message=c.execute("SELECT * FROM messages WHERE id=?",(cycle["message_id"],)).fetchone()
                if message and message["status"]=="sent":settle(c,store,message,message["sent_at"] or now)
                elif not message or message["status"] not in {"queued","sending"}:stop(c,cycle,"Tentativa sem confirmação; não repetir",now,"paused")
                continue
            if cycle["due_at"]>now:continue
            config=json.loads(cycle["config"]);due=next_window(config,now)
            if due>now:
                c.execute("UPDATE crm_followup_cycles SET due_at=? WHERE id=?",(due,cycle["id"]));continue
            ident=uuid.uuid4().hex;updated=dict(cycle);updated["attempt"]+=1
            body=body_for(c,updated)
            c.execute("""INSERT INTO messages(id,channel_id,contact_id,body,direction,origin,recipient_phone,sender_name,followup_cycle_id,followup_attempt,created_at)
                VALUES(?,?,?,?,'out','followup',?,'Assistente virtual',?,?,?)""",(ident,cycle["channel_id"],cycle["contact_id"],body,cycle["phone"],cycle["id"],updated["attempt"],now))
            c.execute("UPDATE crm_followup_cycles SET attempt=?,message_id=?,updated_at=? WHERE id=?",(updated["attempt"],ident,now,cycle["id"]))
            log(c,cycle["id"],"queued","Uma tentativa reservada na fila compartilhada",now)
        return bool(cycles)


def message_allowed(c, store, message, now):
    cycle=c.execute("SELECT * FROM crm_followup_cycles WHERE id=?",(message["followup_cycle_id"],)).fetchone()
    allowed=getattr(store,"followup_permissions",None)
    reason=None
    if not cycle or cycle["status"]!="waiting" or cycle["message_id"]!=message["id"] or cycle["attempt"]!=message["followup_attempt"]:
        reason="Tentativa interrompida ou divergente"
    elif not callable(allowed) or not allowed(dict(cycle)):
        reason="Responsável sem acesso atual"
    else:
        invalid=guard(c,store,cycle,now,own_message=message["id"])
        if invalid:
            if invalid[0]=="defer":return False
            reason=invalid[1]
        elif cycle["due_at"]>now or next_window(json.loads(cycle["config"]),now)>now:
            return False
        elif (message["body"],message["channel_id"],message["contact_id"],message["recipient_phone"],message["origin"],message["media_id"],message["sender_user_id"])!=(body_for(c,cycle),cycle["channel_id"],cycle["contact_id"],cycle["phone"],"followup","",""):
            reason="Conteúdo ou destinatário diferente do acompanhamento autorizado"
    if reason:
        if cycle and cycle["status"]=="waiting":stop(c,cycle,reason,now,"paused")
        c.execute("UPDATE messages SET status='skipped',skip_reason=? WHERE id=? AND status IN ('queued','sending')",(reason,message["id"]))
        return False
    return True


def channel_gap(c, doctor, baseline):
    values=[json.loads(r[0]).get("spacing_seconds",180) for r in c.execute("SELECT f.config FROM followup_policies f JOIN pipelines p ON p.id=f.pipeline_id WHERE f.enabled=1 AND p.doctor_id=?",(doctor,))]
    return max([baseline,*values])
