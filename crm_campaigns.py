"""Reviewed, frozen audiences and truthful server-side campaign reporting."""
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import time
import uuid

from crm_filters import appointment_active, appointment_ready, financial_ready, fold, matches, ordered, parse_filters
from crm_store import Conflict, TZ, integer, text
from crm_access import pipeline_visible, check_row

FINGERPRINT_FIELDS=("id","contact_id","revision","consent_at","phone","name","opted_out","experts_status","experts_uuid","service_location_revision")


def rows_fingerprint(rows):
    values=[tuple(r[k] for k in FINGERPRINT_FIELDS) for r in sorted(rows,key=lambda r:r["id"])]
    return hashlib.sha256(json.dumps(values,ensure_ascii=False).encode()).hexdigest()


def fingerprint(c, doctor, ids):
    wanted = set(ids)
    rows = c.execute("""SELECT l.id,l.contact_id,l.revision,l.consent_at,ct.phone,
        CASE WHEN p.course_key<>'' AND l.source_name<>'' THEN l.source_name ELSE ct.name END name,ct.opted_out,
        ct.experts_status,ct.experts_uuid,ct.service_location_revision FROM leads l JOIN contacts ct ON ct.id=l.contact_id
        JOIN pipelines p ON p.id=l.pipeline_id
        WHERE l.doctor_id=? AND l.deleted_at=0 ORDER BY l.id""", (doctor,)).fetchall()
    rows = [dict(r) for r in rows if r["id"] in wanted]
    from crm_contact_phone import attach
    attach(c, rows)
    return rows_fingerprint(rows)


def selection(store, payload):
    doctor = payload.get("doctor_id")
    filters = parse_filters(payload.get("filters", {}))
    excluded = payload.get("exclude_ids", [])
    stages = payload.get("stage_ids", [])
    pipelines = payload.get("pipeline_ids", [])
    included = payload.get("lead_ids")
    removed = payload.get("exclude_lead_ids", [])
    if any(not isinstance(x, list) or len(x)>5000 or any(not isinstance(k,str) for k in x) for x in (excluded,stages,pipelines,removed)) or (included is not None and (not isinstance(included,list) or len(included)>5000 or any(not isinstance(k,str) for k in included))):
        raise ValueError("Selecao de destinatarios invalida.")
    if type(payload.get("stages_explicit",False)) is not bool:
        raise ValueError("Selecao de fases invalida.")
    store.reconcile_patients()
    with store.db() as c:
        doc = store.row(c, "doctors", doctor)
        uncertain_contacts,uncertain_phones=set(),set()
        if payload.get("channel_id"):
            channel=store.owned(c,"channels",payload["channel_id"],doctor)
            for row in c.execute("SELECT contact_id,recipient_phone FROM messages WHERE channel_id=? AND status IN ('sending','unknown')",(channel["id"],)):
                uncertain_contacts.add(row[0])
                if row[1]:uncertain_phones.add(row[1])
        pipeline = payload.get("pipeline_id", "")
        if pipeline:
            store.owned(c, "pipelines", pipeline, doctor)
            pipelines = pipelines or [pipeline]
        for key in pipelines:
            store.owned(c, "pipelines", key, doctor)
        if included is not None:
            for key in included:
                store.owned(c,"leads",key,doctor)
            included=set(included)
        removed=set(removed)
        for stage in stages:
            row = store.row(c,"stages",stage)
            owner = store.owned(c,"pipelines",row["pipeline_id"],doctor)
            if pipelines and owner["id"] not in pipelines:
                raise ValueError("Fase de outro funil.")
        leads = [dict(r) for r in c.execute("""SELECT l.*,ct.name,ct.phone,ct.opted_out,ct.experts_uuid,ct.experts_status,
            ct.service_location,ct.service_location_source,ct.service_location_revision,s.kind,p.institute_key,p.course_key
            FROM leads l JOIN contacts ct ON ct.id=l.contact_id JOIN stages s ON s.id=l.stage_id
            JOIN pipelines p ON p.id=l.pipeline_id WHERE l.doctor_id=? AND l.deleted_at=0 ORDER BY l.updated_at DESC,l.id""", (doctor,)) if pipeline_visible(dict(r))]
        from crm_contact_phone import attach
        attach(c, leads)
        store.attach_customer_values(c,leads,doc["name"],bool(filters["search"]))
        if appointment_active(filters):
            store.attach_appointment_filters(leads,doc["name"],filters)
        def in_scope(l):
            return ((not pipelines or l["pipeline_id"] in pipelines)
                    and (l["stage_id"] in stages if payload.get("stages_explicit") else not stages or l["stage_id"] in stages)
                    and (included is None or l["id"] in included) and l["id"] not in removed)
        coverage = len({l["contact_id"] for l in leads if in_scope(l) and (not financial_ready(l,filters) or not appointment_ready(l,filters))})
        leads = ordered([l for l in leads if matches(l,filters)],filters)
        catalog = [dict(r) for r in c.execute("SELECT * FROM pipelines WHERE doctor_id=? ORDER BY name",(doctor,)) if pipeline_visible(dict(r))]
        for p in catalog:
            members=[l for l in leads if l["pipeline_id"]==p["id"]]
            p["count"]=len(members)
            p["stages"]=[{**dict(s),"count":sum(l["stage_id"]==s["id"] for l in members)} for s in c.execute("SELECT * FROM stages WHERE pipeline_id=? ORDER BY position",(p["id"],))]
        leads=[l for l in leads if in_scope(l)]
        reasons=Counter();selected=[];contacts=set();duplicates=0
        for l in leads:
            reason = "Removido da selecao" if l["contact_id"] in excluded else "Sem telefone" if not l["phone"] else "Descadastrado" if l["opted_out"] else "Cadastro a conferir" if l["experts_status"] == "review" else ""
            if not reason and (l["contact_id"] in uncertain_contacts or l["phone"] in uncertain_phones):
                reason="Envio anterior em andamento ou sem confirmacao neste numero"
            duplicate=not reason and l["contact_id"] in contacts
            if reason:
                reasons[reason]+=1
            elif duplicate:
                duplicates+=1
            else:
                contacts.add(l["contact_id"]);selected.append(l)
            l["campaign_reason"]=reason or ("Contato repetido" if duplicate else "")
            l.pop("_experts_search",None)
        result={"selected":len(leads),"recipients":len(selected),"duplicates":duplicates,"excluded":sum(reasons.values()),
                "reasons":dict(reasons),"incomplete":coverage}
    return leads,selected,catalog,result


def candidates(store,payload):
    page=integer(payload.get("page",1),1,100000)
    order=payload.get("order_by","")
    if not isinstance(order,str) or order not in {"","name","pipeline","spent","quotes","course_paid","course_value","eligibility"}:
        raise ValueError("Ordenacao de destinatarios invalida.")
    direction=payload.get("order_dir","desc" if order in {"spent","quotes","course_paid","course_value"} else "asc")
    if not isinstance(direction,str) or direction not in {"asc","desc"}:
        raise ValueError("Ordenacao de destinatarios invalida.")
    rows,selected,catalog,result=selection(store,payload)
    if order:
        rows=sort_candidates(rows,catalog,order,direction)
    start=(page-1)*50
    return {**result,"pipelines":catalog,"items":rows[start:start+50],"page":page,"page_size":50,"has_more":len(rows)>start+50}


def sort_candidates(rows,catalog,order,direction):
    pipelines={p["id"]:fold(p["name"]) for p in catalog}
    stages={s["id"]:s["position"] for p in catalog for s in p["stages"]}
    reason_rank={"":0,"Cadastro a conferir":1,"Descadastrado":2,"Contato repetido":3,"Removido da selecao":4}
    base=sorted(rows,key=lambda l:(fold(l["name"]),l["id"]))
    if order in {"course_paid","course_value"}:
        ready=[l for l in base if order=="course_value" or l.get("course_data",{}).get("state")=="ready"]
        missing=[l for l in base if l not in ready]
        return sorted(ready,key=lambda l:l.get("course_data",{}).get("paid_cents",0) if order=="course_paid" else l["value_cents"],reverse=direction=="desc")+missing
    if order in {"spent","quotes"}:
        state,field=("sales_state","sales_cents") if order=="spent" else ("quotes_state","pending_quotes_cents")
        known=lambda l:l.get("customer_history",{}).get(state)=="ready" and type(l["customer_history"].get(field)) is int
        ready=[l for l in base if known(l)]
        missing=[l for l in base if not known(l)]
        # Unknown or partial amounts stay last, never masquerading as zero.
        return sorted(ready,key=lambda l:l["customer_history"][field],reverse=direction=="desc")+missing
    key={"name":lambda l:fold(l["name"]),
         "pipeline":lambda l:(pipelines[l["pipeline_id"]],stages[l["stage_id"]]),
         "eligibility":lambda l:reason_rank.get(l["campaign_reason"],99)}[order]
    return sorted(base,key=key,reverse=direction=="desc")


def audience(store, payload, actor):
    leads,selected,_,result=selection(store,payload)
    if len(leads)>5000:
        raise ValueError("A selecao ultrapassa 5000 oportunidades. Refine os filtros.")
    doctor=payload.get("doctor_id")
    ids=[l["id"] for l in selected]
    with store.db() as c:
        key=uuid.uuid4().hex
        c.execute("DELETE FROM campaign_audiences WHERE expires_at<? AND campaign_id=''",(time.time()-86400,))
        c.execute("INSERT INTO campaign_audiences VALUES(?,?,?,?,?,?,?)",(key,doctor,str(actor["id"]),json.dumps({"ids":ids,"summary":result}),rows_fingerprint(selected),time.time()+1800,""))
    return {"preview_id":key,**result,"sample":[{"id":l["id"],"contact_id":l["contact_id"],"name":l["name"],"phone":l["phone"]} for l in selected[:100]],"sample_limit":100}


def create(store,payload,actor,*,editing=False):
    with store.db() as c:
        snapshot=store.row(c,"campaign_audiences",payload.get("preview_id"))
        if snapshot["doctor_id"] != payload.get("doctor_id") or snapshot["actor_id"] != str(actor["id"]):
            raise LookupError()
        data=json.loads(snapshot["payload"])
    return store.campaign({**payload,"lead_ids":data["ids"],"_audience_id":snapshot["id"],
        "_edit_id":text(payload.get("id"),32) if editing else ""},actor["nome"],actor["id"])


def template(store,doctor,key):
    with store.db() as c:
        campaign=store.owned(c,"campaigns",key,doctor)
        contacts={r[0] for r in c.execute("SELECT contact_id FROM messages WHERE campaign_id=?",(key,))}
        snapshot=c.execute("SELECT payload FROM campaign_audiences WHERE campaign_id=?",(key,)).fetchone()
        original=set(json.loads(snapshot[0])["ids"]) if snapshot else None
        ids,seen=[],set()
        rows=c.execute("""SELECT l.id,l.contact_id,p.id pipeline_id,p.doctor_id,p.institute_key FROM leads l JOIN pipelines p ON p.id=l.pipeline_id
            WHERE l.doctor_id=? AND l.deleted_at=0 ORDER BY l.created_at,l.id""",(doctor,)).fetchall()
        # Recipients are contacts; a replaced opportunity must not erase that audience.
        for row in sorted(rows,key=lambda item: original is not None and item["id"] not in original):
            if row["contact_id"] in contacts and row["contact_id"] not in seen and pipeline_visible(dict(row)):
                ids.append(row["id"])
                seen.add(row["contact_id"])
        campaign["weekdays"]=json.loads(campaign["weekdays"])
        return {"campaign":campaign,"audience":{"lead_ids":ids,"pipeline_id":"","filters":{}},
                "unavailable":len(contacts)-len(seen)}


def period(query):
    today=datetime.now(TZ).date()
    start=datetime.fromisoformat(query.get("from",[(today-timedelta(days=29)).isoformat()])[0]).date()
    end=datetime.fromisoformat(query.get("to",[today.isoformat()])[0]).date()
    if end<start or (end-start).days>365:
        raise ValueError("Informe um periodo de ate 366 dias.")
    return start,end,datetime.combine(start,datetime.min.time(),TZ).timestamp(),datetime.combine(end+timedelta(days=1),datetime.min.time(),TZ).timestamp()


def dashboard(store,doctor,query):
    start,end,ts,te=period(query)
    status=query.get("status",[""])[0]
    if status not in {"","draft","running","paused","done","cancelled"}:
        raise ValueError("Status invalido.")
    page=integer(query.get("page",[1])[0],1,10000)
    with store.db() as c:
        store.row(c,"doctors",doctor)
        visible=[]
        for row in c.execute("SELECT * FROM campaigns WHERE doctor_id=? AND created_at>=? AND created_at<?",(doctor,ts,te)):
            try:
                check_row(c,"campaigns",dict(row))
                visible.append(dict(row))
            except PermissionError:
                continue
        allowed={r["id"] for r in visible}
        counts=dict(Counter(r["status"] for r in visible))
        rows=[dict(r) for r in c.execute("""SELECT ca.*,ch.name channel_name,ch.phone channel_phone,ch.state channel_state,
            ch.daily_limit,ch.next_send_at,ch.archived_at channel_archived,ch.doctor_id channel_doctor FROM campaigns ca JOIN channels ch ON ch.id=ca.channel_id
            WHERE ca.doctor_id=? AND ca.created_at>=? AND ca.created_at<? AND (?='' OR ca.status=?)
            ORDER BY ca.created_at DESC,ca.id""",(doctor,ts,te,status,status)) if r["id"] in allowed][(page-1)*20:page*20]
        for row in rows:
            row["counts"]={r[0]:r[1] for r in c.execute("SELECT status,count(*) FROM messages WHERE campaign_id=? GROUP BY status",(row["id"],))}
            row["weekdays"]=json.loads(row["weekdays"])
            row["total"]=sum(row["counts"].values())
            row["resume_excluded"]=c.execute("SELECT count(*) FROM messages WHERE campaign_id=? AND status='unknown' AND automation_excluded_at>0",(row["id"],)).fetchone()[0]
            row["resume_unconfirmed"]=row["counts"].get("unknown",0)-row["resume_excluded"]
            row["start_blocker"]=""
            if not row["counts"].get("queued",0):
                row["start_blocker"]="Nenhum destinatario na fila. Duplique para um novo envio."
            elif row["channel_doctor"]!=doctor:
                row["start_blocker"]="Numero transferido para outro profissional. Duplique e escolha outra conexao."
            elif row["channel_archived"] or row["channel_state"]!="open":
                row["start_blocker"]="Reconecte o numero para retomar."
            elif c.execute("SELECT 1 FROM messages WHERE channel_id=? AND status='sending'",(row["channel_id"],)).fetchone():
                row["start_blocker"]="Aguarde o envio em andamento e atualize."
            row["progress"]=round(100*(row["total"]-row["counts"].get("queued",0)-row["counts"].get("sending",0))/row["total"]) if row["total"] else 0
            row["daily_used"]=c.execute("SELECT count(*) FROM messages WHERE channel_id=? AND campaign_id IS NOT NULL AND (sent_at>=? OR claimed_at>=?)",(row["channel_id"],datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp(),datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())).fetchone()[0]
        metrics=Counter(r[0] for r in c.execute("""SELECT m.status,m.campaign_id FROM messages m JOIN campaigns ca ON ca.id=m.campaign_id
            WHERE ca.doctor_id=? AND m.claimed_at>=? AND m.claimed_at<?""",(doctor,ts,te)) if r[1] in allowed)
        sent=[r for r in c.execute("SELECT m.sent_at,m.campaign_id FROM messages m JOIN campaigns ca ON ca.id=m.campaign_id WHERE ca.doctor_id=? AND m.status='sent' AND m.sent_at>=? AND m.sent_at<?",(doctor,ts,te)) if r[1] in allowed]
        days=Counter(datetime.fromtimestamp(r[0],TZ).date().isoformat() for r in sent)
        dedup=sum(r["duplicates_count"] for r in visible)
        total=counts.get(status,0) if status else sum(counts.values())
    return {"campaigns":rows,"counts":counts,"total":total,"page":page,"has_more":page*20<total,"from":start.isoformat(),"to":end.isoformat(),
            "metrics":{"sent":len(sent),"unknown":metrics.get("unknown",0),"active":counts.get("running",0),"duplicates":dedup},
            "daily":[{"day":(start+timedelta(days=n)).isoformat(),"sent":days[(start+timedelta(days=n)).isoformat()]} for n in range((end-start).days+1)]}


def detail(store,doctor,key,query):
    page=integer(query.get("page",[1])[0],1,10000)
    status=query.get("status",[""])[0]
    search=text(query.get("search",[""])[0],100,False).casefold()
    if status not in {"","sent","queued","sending","unknown","skipped","cancelled"}:
        raise ValueError("Status invalido.")
    with store.db() as c:
        campaign=store.owned(c,"campaigns",key,doctor)
        rows=[dict(r) for r in c.execute("SELECT m.id,m.status,m.created_at,m.sent_at,m.claimed_at,m.skip_reason,m.sender_name,m.automation_excluded_at,coalesce(nullif(m.recipient_phone,''),ct.phone) phone,ct.name FROM messages m JOIN contacts ct ON ct.id=m.contact_id WHERE m.campaign_id=? ORDER BY m.created_at,m.id",(key,))]
    rows=[r for r in rows if (not status or r["status"]==status) and (not search or search in (r["name"]+" "+r["phone"]).casefold())]
    return {"campaign":campaign,"items":rows[(page-1)*50:page*50],"total":len(rows),"page":page,"has_more":page*50<len(rows)}
