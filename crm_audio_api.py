"""Manager controls only; transcripts are exposed through scoped conversations."""
import json
import crm_audio
from crm_quality_api import provider_ready
from crm_store import text
from task_api import read_body


def handle(handler,store,action,query,user):
    if not user.get("is_master"):raise PermissionError("Transcrição configurada somente pelo gestor Master.")
    doctor=query.get("doctor",[""])[0]
    if handler.command=="GET" and action=="audio":
        return handler.auth_json({"ok":True,"provider_ready":provider_ready(),**crm_audio.overview(store,doctor)})
    if handler.command!="POST":return handler.auth_json({"ok":False},405)
    if not handler.same_origin_request():return handler.auth_json({"ok":False},403)
    p=json.loads(read_body(handler,16*1024))
    if not isinstance(p,dict) or p.get("doctor_id")!=doctor:raise ValueError("Profissional inválido.")
    result={}
    if action=="audio/settings":
        if p.get("enabled") and not provider_ready():raise ValueError("Configure a API OpenAI da clínica antes de ativar.")
        result["settings"]=crm_audio.save(store,doctor,p,user["id"])
    elif action=="audio/batch-preview":result["batch"]=crm_audio.preview_batch(store,doctor,p,user["id"])
    elif action in {"audio/batch-apply","audio/retry"}:
        if p.get("confirm") is not True:raise ValueError("Confirme o processamento dos áudios pela OpenAI e o consumo da API.")
        ident=text(p.get("id"),64,False)
        if not provider_ready():raise ValueError("Configure a API OpenAI da clínica antes de continuar.")
        if action=="audio/retry":crm_audio.retry(store,doctor,ident,user["id"])
        else:result.update(crm_audio.apply_batch(store,doctor,ident,user["id"]))
    else:raise LookupError()
    handler.server.auth_store.audit_event("crm_"+action,user["id"],details={"clinic":"vielle","doctor_id":doctor,"id":p.get("id","")})
    return handler.auth_json({"ok":True,**result})
