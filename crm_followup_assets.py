"""Private, funnel-scoped reusable assets; never borrow a patient's attachment."""
import hashlib
import os
import time
import uuid
from urllib.parse import urlencode, unquote

import crm_media


def schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS crm_followup_assets(
        id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL,pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
        user_id TEXT NOT NULL,request_id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,
        mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,created_at REAL NOT NULL,
        UNIQUE(user_id,request_id))""")
    if "followup_asset_id" not in {r[1] for r in c.execute("PRAGMA table_info(message_media)")}:
        c.execute("ALTER TABLE message_media ADD COLUMN followup_asset_id TEXT NOT NULL DEFAULT ''")


def owned(c, key, pipeline):
    row=c.execute("SELECT * FROM crm_followup_assets WHERE id=? AND pipeline_id=?",(key,pipeline)).fetchone()
    if not row:raise ValueError("Anexo de follow-up indisponível neste funil.")
    return dict(row)


def public(row):
    return {k:row[k] for k in ("id","name","kind","mime","size")} | {
        "url":"/api/crm/followups/media?"+urlencode({"clinic":"vielle","doctor":row["doctor_id"],"pipeline":row["pipeline_id"],"id":row["id"]})}


def upload(store, doctor, pipeline, user, request, name, kind, data):
    from crm_followup_settings import owner
    if not user.get("is_master"):raise PermissionError("Somente o Master pode anexar arquivos à régua.")
    if not isinstance(request,str) or not 1<=len(request)<=80:raise ValueError("Identificador de upload inválido.")
    name,mime=crm_media.validate(name,kind,data)
    digest=hashlib.sha256(data).hexdigest();path=None
    try:
        with store.db() as c:
            c.execute("BEGIN IMMEDIATE");owner(store,c,doctor,pipeline)
            policy=c.execute("SELECT enabled FROM followup_policies WHERE pipeline_id=?",(pipeline,)).fetchone()
            if policy and policy[0]:raise ValueError("Desligue a régua antes de editar os anexos.")
            old=c.execute("SELECT * FROM crm_followup_assets WHERE user_id=? AND request_id=?",(str(user["id"]),request)).fetchone()
            if old:
                if (old["pipeline_id"],old["kind"],old["sha256"],old["name"])!=(pipeline,kind,digest,name):raise ValueError("Upload já usado para outro arquivo.")
                return public(dict(old))
            if crm_media.storage_used(c)+len(data)>1024*crm_media.MB:raise ValueError("Armazenamento de anexos cheio.")
            count=c.execute("SELECT count(*) FROM crm_followup_assets WHERE pipeline_id=?",(pipeline,)).fetchone()[0]
            if count>=200:raise ValueError("Limite de arquivos deste funil alcançado.")
            key=uuid.uuid4().hex;path=crm_media.path_for(store,key);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),"wb") as target:target.write(data)
            c.execute("INSERT INTO crm_followup_assets VALUES(?,?,?,?,?,?,?,?,?,?,?)",(key,doctor,pipeline,str(user["id"]),request,name,kind,mime,len(data),digest,time.time()))
            result=public(owned(c,key,pipeline))
        return result
    except Exception:
        if path:path.unlink(missing_ok=True)
        raise


def content(store, row):
    path=crm_media.path_for(store,row["id"])
    if path.is_symlink() or not path.is_file():raise ValueError("Anexo de follow-up indisponível.")
    with path.open("rb") as source:data=source.read(crm_media.LIMITS[row["kind"]]+1)
    if len(data)!=row["size"] or hashlib.sha256(data).hexdigest()!=row["sha256"]:raise ValueError("Anexo de follow-up alterado ou indisponível.")
    return data


def bind(c, store, cycle, message, asset_id, now):
    asset=owned(c,asset_id,cycle["pipeline_id"]);content(store,asset)
    key=uuid.uuid4().hex
    c.execute("""INSERT INTO message_media(id,doctor_id,source_channel_id,contact_id,user_id,upload_request,
        name,mime,kind,size,sha256,created_at,message_id,download_state,followup_asset_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'ready',?)""",(key,cycle["doctor_id"],cycle["channel_id"],cycle["contact_id"],
        cycle["activator_id"],"followup:"+message,asset["name"],asset["mime"],asset["kind"],asset["size"],asset["sha256"],now,message,asset_id))
    return key


def matches(c, cycle, message, asset_id):
    media=c.execute("SELECT * FROM message_media WHERE id=?",(message["media_id"],)).fetchone()
    asset=owned(c,asset_id,cycle["pipeline_id"])
    return bool(media and (media["message_id"],media["contact_id"],media["source_channel_id"],media["doctor_id"],media["followup_asset_id"],media["sha256"])
        ==(message["id"],cycle["contact_id"],cycle["channel_id"],cycle["doctor_id"],asset_id,asset["sha256"]))


def handle(handler,store,doctor,query,user):
    from crm_followup_settings import owner
    pipeline=query.get("pipeline",[""])[0]
    with store.db() as c:owner(store,c,doctor,pipeline)
    if handler.command=="GET":
        with store.db() as c:row=owned(c,query.get("id",[""])[0],pipeline)
        return crm_media.stream(handler,row,crm_media.path_for(store,row["id"]))
    if handler.command!="POST":return handler.auth_json({"ok":False},405)
    if not user.get("is_master"):raise PermissionError("Somente o Master pode anexar arquivos à régua.")
    if not handler.same_origin_request():return handler.auth_json({"ok":False},403)
    kind=query.get("kind",[""])[0]
    if kind not in crm_media.LIMITS:raise ValueError("Tipo de anexo inválido.")
    result=upload(store,doctor,pipeline,user,handler.headers.get("X-Upload-Id",""),unquote(handler.headers.get("X-File-Name","")),kind,crm_media.read_body(handler,crm_media.LIMITS[kind]))
    handler.server.auth_store.audit_event("crm_followup_asset_uploaded",user["id"],details={"pipeline_id":pipeline,"id":result["id"],"kind":kind})
    return handler.auth_json({"ok":True,"media":result})
