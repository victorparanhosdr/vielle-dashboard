"""Opt-in attendance transcription; scoped queue, shared cache and bounded billing."""
from datetime import datetime
import io
import json
import logging
import math
import time
import uuid
import wave

from crm_appointments import TZ
from crm_store import Conflict, integer
from crm_quality_provider import QualityError, classify, audio_model


def schema(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS crm_audio_settings(
            doctor_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,
            daily_minutes INTEGER NOT NULL DEFAULT 60,revision INTEGER NOT NULL DEFAULT 0,
            enabled_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL,updated_by TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS crm_audio_jobs(
            id TEXT PRIMARY KEY,media_id TEXT NOT NULL UNIQUE,doctor_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,contact_id TEXT NOT NULL,message_id TEXT NOT NULL,
            revision INTEGER NOT NULL,source TEXT NOT NULL,created_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',error_code TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,finished_at REAL NOT NULL DEFAULT 0,
            claimed_at REAL NOT NULL DEFAULT 0,seconds INTEGER NOT NULL DEFAULT 0,
            quota_day TEXT NOT NULL DEFAULT '',retry_from TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS crm_audio_queue ON crm_audio_jobs(status,created_at);
        CREATE TABLE IF NOT EXISTS crm_audio_usage(
            attempt_id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL,day TEXT NOT NULL,seconds INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS crm_audio_usage_day ON crm_audio_usage(doctor_id,day);
        CREATE TABLE IF NOT EXISTS crm_audio_batches(
            id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL,revision INTEGER NOT NULL,ids TEXT NOT NULL,
            created_by TEXT NOT NULL,created_at REAL NOT NULL,committed_at REAL NOT NULL DEFAULT 0);
    """)


def day(now):
    return datetime.fromtimestamp(now,TZ).strftime("%Y-%m-%d")


def settings(c,doctor):
    row=c.execute("SELECT * FROM crm_audio_settings WHERE doctor_id=?",(doctor,)).fetchone()
    return dict(row) if row else {"doctor_id":doctor,"enabled":False,"daily_minutes":60,"revision":0,"enabled_at":0,"updated_at":0,"updated_by":""}


def save(store,doctor,p,actor,now=None):
    now=now or time.time()
    if type(p.get("enabled")) is not bool:raise ValueError("Ativação inválida.")
    minutes=integer(p.get("daily_minutes"),1,1440)
    if p["enabled"] and p.get("confirm") is not True:raise ValueError("Autorize o envio dos áudios à OpenAI e o consumo da API.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE");store.row(c,"doctors",doctor)
        old=settings(c,doctor)
        if integer(p.get("revision"),0,2**31)!=old["revision"]:raise Conflict("Configuração alterada. Atualize antes de salvar.")
        c.execute("""INSERT INTO crm_audio_settings VALUES(?,?,?,?,?,?,?) ON CONFLICT(doctor_id) DO UPDATE SET
            enabled=excluded.enabled,daily_minutes=excluded.daily_minutes,revision=excluded.revision,
            enabled_at=excluded.enabled_at,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (doctor,int(p["enabled"]),minutes,old["revision"]+1,now if p["enabled"] else 0,now,str(actor)))
        c.execute("UPDATE crm_audio_jobs SET status='cancelled',error_code='scope_changed',error='Configuração alterada; pendência cancelada',finished_at=? WHERE doctor_id=? AND status IN ('queued','running','limited')",(now,doctor))
        return settings(c,doctor)


def enqueue(c,store,message_id,now,*,source="new",actor="",revision=None):
    row=c.execute("""SELECT mm.*,m.created_at message_time,m.direction,m.status message_status,
        ch.archived_at,ch.doctor_id channel_doctor FROM message_media mm JOIN messages m ON m.id=mm.message_id
        JOIN channels ch ON ch.id=m.channel_id WHERE m.id=? AND m.media_id=mm.id AND mm.kind='audio'
        AND mm.source_channel_id=m.channel_id AND mm.contact_id=m.contact_id""",(message_id,)).fetchone()
    if not row or row["archived_at"] or row["doctor_id"]!=row["channel_doctor"] or row["message_status"] not in {"sent","received"}:return None
    policy=settings(c,row["doctor_id"])
    if not policy["enabled"] or revision is not None and policy["revision"]!=revision:return None
    ident=uuid.uuid4().hex
    c.execute("""INSERT OR IGNORE INTO crm_audio_jobs(id,media_id,doctor_id,channel_id,contact_id,message_id,
        revision,source,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (ident,row["id"],row["doctor_id"],row["source_channel_id"],row["contact_id"],message_id,
         policy["revision"],source,str(actor or policy["updated_by"]),now))
    return c.execute("SELECT id FROM crm_audio_jobs WHERE media_id=?",(row["id"],)).fetchone()[0]


def received(c,store,message_id,now):
    # Transcription must never roll back an accepted send or incoming message.
    c.execute("SAVEPOINT audio_enroll")
    try:enqueue(c,store,message_id,now)
    except Exception:
        c.execute("ROLLBACK TO audio_enroll");logging.warning("CRM audio enrollment deferred; message preserved")
    finally:c.execute("RELEASE audio_enroll")


def valid(c,store,job,permitted):
    policy=settings(c,job["doctor_id"])
    if not policy["enabled"] or policy["revision"]!=job["revision"] or not permitted(policy["updated_by"]) or not permitted(job["created_by"]):return False
    try:
        channel,_=store.conversation_contact(c,job["doctor_id"],job["channel_id"],job["contact_id"])
        row=store.owned(c,"message_media",job["media_id"],job["doctor_id"])
        message=store.row(c,"messages",job["message_id"])
        return not channel["archived_at"] and (row["source_channel_id"],row["contact_id"],row["message_id"],row["kind"])==(job["channel_id"],job["contact_id"],job["message_id"],"audio") and (message["channel_id"],message["contact_id"],message["media_id"])==(job["channel_id"],job["contact_id"],job["media_id"])
    except (ValueError,LookupError,PermissionError):return False


def tick(store,provider,permitted,now=None,*,gateway=None):
    now=now or time.time()
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE crm_audio_jobs SET status='failed',error_code='analysis_interrupted',error='Transcrição interrompida; sem repetição automática',finished_at=? WHERE status='running' AND claimed_at<?",(now,now-180))
        c.execute("UPDATE crm_audio_jobs SET status='queued' WHERE status='limited' AND quota_day<>?",(day(now),))
        row=c.execute("SELECT * FROM crm_audio_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row:return False
        job=dict(row)
        if not valid(c,store,job,permitted):
            c.execute("UPDATE crm_audio_jobs SET status='cancelled',error_code='scope_changed',error='Autorização ou conversa alterada',finished_at=? WHERE id=?",(now,job["id"]));return True
        c.execute("UPDATE crm_audio_jobs SET status='running',claimed_at=?,error='',error_code='' WHERE id=?",(now,job["id"]))
    def allowed():
        with store.db() as c:
            current=c.execute("SELECT status,claimed_at FROM crm_audio_jobs WHERE id=?",(job["id"],)).fetchone()
            return bool(current and current["status"]=="running" and current["claimed_at"]==now and valid(c,store,job,permitted))
    def reserve(c,wav):
        active=c.execute("SELECT status,claimed_at FROM crm_audio_jobs WHERE id=?",(job["id"],)).fetchone()
        if not active or active["status"]!="running" or active["claimed_at"]!=now or not valid(c,store,job,permitted):raise QualityError("scope_changed")
        with wave.open(io.BytesIO(wav),"rb") as audio:seconds=math.ceil(audio.getnframes()/audio.getframerate())
        moment=time.time();today=day(moment);policy=settings(c,job["doctor_id"])
        if seconds>policy["daily_minutes"]*60:raise QualityError("audio_budget_size")
        used=c.execute("SELECT coalesce(sum(seconds),0) FROM crm_audio_usage WHERE doctor_id=? AND day=?",(job["doctor_id"],today)).fetchone()[0]
        if used+seconds>policy["daily_minutes"]*60:raise QualityError("audio_daily_limit")
        c.execute("INSERT INTO crm_audio_usage VALUES(?,?,?,?)",(uuid.uuid4().hex,job["doctor_id"],today,seconds))
        c.execute("UPDATE crm_audio_jobs SET seconds=?,quota_day=? WHERE id=?",(seconds,today,job["id"]))
    try:
        from crm_quality_audio import transcript
        with store.db() as c:media=store.owned(c,"message_media",job["media_id"],job["doctor_id"])
        job["_audio_model"]=audio_model()
        transcript(store,job,{"media_id":job["media_id"],"message_id":job["message_id"],"media_sha256":media["sha256"]},provider,gateway,allowed,before_call=reserve)
        if not allowed():raise QualityError("scope_changed")
        with store.db() as c:c.execute("UPDATE crm_audio_jobs SET status='done',finished_at=? WHERE id=? AND status='running' AND claimed_at=?",(time.time(),job["id"],now))
    except Exception as exc:
        failure=classify(exc)
        status="cancelled" if failure.code=="scope_changed" else "limited" if failure.code=="audio_daily_limit" else "failed"
        with store.db() as c:c.execute("UPDATE crm_audio_jobs SET status=?,error_code=?,error=?,finished_at=?,quota_day=? WHERE id=? AND status='running' AND claimed_at=?",
            (status,failure.code,str(failure),time.time(),day(time.time()),job["id"],now))
    return True


def attach(c,messages):
    ids=[m["media"]["id"] for m in messages if m.get("media",{}).get("kind")=="audio"]
    if not ids:return
    placeholders=",".join("?" for _ in ids)
    done=c.execute(f"""SELECT t.media_id,t.text,t.model FROM crm_audio_transcripts t JOIN message_media mm ON mm.id=t.media_id
        WHERE t.media_id IN ({placeholders}) AND t.sha256=mm.sha256 AND t.status='done' ORDER BY t.updated_at""",ids).fetchall()
    ready={r["media_id"]:{"status":"done","text":r["text"],"automatic":True} for r in done}
    jobs={r["media_id"]:dict(r) for r in c.execute(f"SELECT media_id,status,error_code FROM crm_audio_jobs WHERE media_id IN ({placeholders})",ids)}
    for message in messages:
        if message.get("media",{}).get("id") in ids:
            ident=message["media"]["id"]
            message["media"]["transcript"]=ready.get(ident) or jobs.get(ident) or {"status":"not_requested"}


def overview(store,doctor):
    now=time.time()
    with store.db() as c:
        store.row(c,"doctors",doctor);policy=settings(c,doctor)
        seconds=c.execute("SELECT coalesce(sum(seconds),0) FROM crm_audio_usage WHERE doctor_id=? AND day=?",(doctor,day(now))).fetchone()[0]
        counts={r[0]:r[1] for r in c.execute("SELECT status,count(*) FROM crm_audio_jobs WHERE doctor_id=? GROUP BY status",(doctor,))}
        failed=[dict(r) for r in c.execute("SELECT id,error,created_at FROM crm_audio_jobs WHERE doctor_id=? AND status='failed' ORDER BY created_at DESC LIMIT 20",(doctor,))]
        return {"settings":policy,"used_seconds":seconds,"day":day(now),"counts":counts,"failed":failed}


def retry(store,doctor,ident,actor):
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        job=store.owned(c,"crm_audio_jobs",ident,doctor);policy=settings(c,doctor)
        if not policy["enabled"] or job["status"]!="failed":raise ValueError("Ative a transcrição e selecione uma falha atual.")
        store.conversation_contact(c,doctor,job["channel_id"],job["contact_id"])
        c.execute("UPDATE crm_audio_jobs SET status='queued',revision=?,retry_from=?,created_by=?,error='',error_code='',created_at=?,claimed_at=0 WHERE id=? AND status='failed'",
            (policy["revision"],uuid.uuid4().hex,str(actor),time.time(),ident))


def preview_batch(store,doctor,p,actor):
    limit=integer(p.get("limit",50),1,100)
    # The date window is explicit; a preview never downloads or transcribes media.
    try:
        start=datetime.strptime(p.get("from",""),"%Y-%m-%d").replace(tzinfo=TZ).timestamp()
        end=datetime.strptime(p.get("to",""),"%Y-%m-%d").replace(tzinfo=TZ).timestamp()+86400
    except (ValueError,TypeError):raise ValueError("Informe as datas do lote.") from None
    if end<=start or end-start>366*86400:raise ValueError("Selecione um período de até um ano.")
    with store.db() as c:
        store.row(c,"doctors",doctor);policy=settings(c,doctor)
        if not policy["enabled"]:raise ValueError("Ative e autorize a transcrição antes de preparar um lote.")
        rows=c.execute("""SELECT mm.id FROM message_media mm JOIN messages m ON m.id=mm.message_id
            JOIN channels ch ON ch.id=m.channel_id WHERE mm.doctor_id=? AND ch.doctor_id=mm.doctor_id
            AND ch.archived_at=0 AND mm.kind='audio' AND m.media_id=mm.id AND mm.source_channel_id=m.channel_id
            AND mm.contact_id=m.contact_id AND m.status IN ('sent','received') AND m.created_at>=? AND m.created_at<?
            AND NOT EXISTS(SELECT 1 FROM crm_audio_jobs j WHERE j.media_id=mm.id)
            ORDER BY m.created_at,mm.id LIMIT ?""",(doctor,start,end,limit+1)).fetchall()
        ids=[r[0] for r in rows[:limit]];ident=uuid.uuid4().hex
        c.execute("INSERT INTO crm_audio_batches(id,doctor_id,revision,ids,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (ident,doctor,policy["revision"],json.dumps(ids),str(actor),time.time()))
        return {"id":ident,"count":len(ids),"has_more":len(rows)>limit,"maximum_minutes":len(ids)*10,"daily_minutes":policy["daily_minutes"]}


def apply_batch(store,doctor,ident,actor):
    now=time.time()
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE");batch=store.owned(c,"crm_audio_batches",ident,doctor)
        if batch["created_by"]!=str(actor) or batch["created_at"]<now-600:raise Conflict("Prévia expirada. Prepare outro lote.")
        if batch["committed_at"]:return {"queued":0,"already_applied":True}
        policy=settings(c,doctor)
        if not policy["enabled"] or policy["revision"]!=batch["revision"]:raise Conflict("Configuração alterada. Prepare outro lote.")
        count=0
        for media_id in json.loads(batch["ids"]):
            media=store.owned(c,"message_media",media_id,doctor)
            store.conversation_contact(c,doctor,media["source_channel_id"],media["contact_id"])
            if not c.execute("SELECT 1 FROM crm_audio_jobs WHERE media_id=?",(media_id,)).fetchone():
                count+=bool(enqueue(c,store,media["message_id"],now,source="batch",actor=actor,revision=policy["revision"]))
        c.execute("UPDATE crm_audio_batches SET committed_at=? WHERE id=?",(now,ident))
        return {"queued":count,"already_applied":False}


def run_worker(auth,stop):
    from app import clinic_context
    from crm_api import store_for
    from crm_gateway import EvolutionGateway
    from crm_quality_provider import transcribe
    store=store_for(auth)
    def permitted(ident):
        user=auth.get_public_user(ident)
        return bool(user and user.get("is_master") and user.get("status")=="active")
    while not stop.wait(3):
        try:
            with clinic_context("vielle"):tick(store,transcribe,permitted,gateway=EvolutionGateway())
        except Exception:logging.warning("CRM attendance transcription unavailable; no implicit retry")
