"""Persistent contact-level follow-up pause; never a consent or send override."""
import json
import time

from task_api import read_body


def schema(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS crm_followup_contact_preferences(
            contact_id TEXT PRIMARY KEY REFERENCES contacts(id),paused INTEGER NOT NULL,
            revision INTEGER NOT NULL,changed_by TEXT NOT NULL,changed_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS crm_followup_contact_events(
            id INTEGER PRIMARY KEY,contact_id TEXT NOT NULL,lead_id TEXT NOT NULL,
            paused INTEGER NOT NULL,revision INTEGER NOT NULL,actor_id TEXT NOT NULL,created_at REAL NOT NULL);
    """)


def preference(c, contact):
    row=c.execute("SELECT paused,revision FROM crm_followup_contact_preferences WHERE contact_id=?",(contact,)).fetchone()
    return {"paused":bool(row[0]) if row else False,"revision":row[1] if row else 1}


def state(c, contact):
    from crm_contact_phone import editable
    return {**preference(c,contact),"can_manage":editable(c,contact)}


def eligible_lead(c, store, doctor, key):
    from crm_followup_settings import owner
    lead=store.owned(c,"leads",key,doctor)
    owner(store,c,doctor,lead["pipeline_id"])
    return lead


def save(store, payload, actor):
    from crm_store import Conflict, integer
    from crm_followup_runtime import stop
    if type(payload.get("paused")) is not bool or payload.get("confirm") is not True:
        raise ValueError("Confirme a pausa individual do follow-up.")
    revision=integer(payload.get("revision"),1,2**63-1)
    now=time.time()
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        lead=eligible_lead(c,store,payload.get("doctor_id"),payload.get("id"))
        current=state(c,lead["contact_id"])
        if not current["can_manage"]:
            raise PermissionError("Contato compartilhado fora do seu acesso. Solicite a pausa ao Master.")
        if revision!=current["revision"]:
            raise Conflict("A pausa foi alterada por outra pessoa. Atualize a ficha.")
        if payload["paused"]==current["paused"]:
            return current
        c.execute("""INSERT INTO crm_followup_contact_preferences VALUES(?,?,?,?,?)
            ON CONFLICT(contact_id) DO UPDATE SET paused=excluded.paused,revision=excluded.revision,
            changed_by=excluded.changed_by,changed_at=excluded.changed_at""",
            (lead["contact_id"],int(payload["paused"]),revision+1,str(actor["id"]),now))
        if payload["paused"]:
            for cycle in c.execute("SELECT * FROM crm_followup_cycles WHERE contact_id=? AND status='waiting'",(lead["contact_id"],)).fetchall():
                stop(c,cycle,"Follow-up pausado na ficha do contato",now,"paused")
        c.execute("INSERT INTO crm_followup_contact_events(contact_id,lead_id,paused,revision,actor_id,created_at) VALUES(?,?,?,?,?,?)",
            (lead["contact_id"],lead["id"],int(payload["paused"]),revision+1,str(actor["id"]),now))
        store.event(c,lead["id"],actor["nome"],"followup-control",
            "Follow-up pausado neste contato." if payload["paused"] else "Pausa de follow-up removida; pendências antigas não retomadas.",now)
        return state(c,lead["contact_id"])


def handle(handler,store,query,user):
    doctor=query.get("doctor",[""])[0]
    if handler.command=="GET":
        with store.db() as c:
            lead=eligible_lead(c,store,doctor,query.get("id",[""])[0])
            result=state(c,lead["contact_id"])
    elif handler.command=="POST":
        if not handler.same_origin_request():
            return handler.auth_json({"ok":False},403)
        payload=json.loads(read_body(handler,16*1024))
        if not isinstance(payload,dict) or payload.get("doctor_id")!=doctor:
            raise ValueError("Profissional inválido para a pausa.")
        result=save(store,payload,user)
        handler.server.auth_store.audit_event("crm_followup_contact_changed",user["id"],
            details={"doctor_id":doctor,"lead_id":payload["id"],"paused":result["paused"],"revision":result["revision"]})
    else:
        return handler.auth_json({"ok":False},405)
    return handler.auth_json({"ok":True,"followup_contact":result})
