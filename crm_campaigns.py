"""Reviewed, frozen audiences and truthful server-side campaign reporting."""
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import time
import uuid

from crm_filters import appointment_active, appointment_ready, financial_ready, matches, ordered, parse_filters
from crm_store import Conflict, TZ, integer, text


def fingerprint(c, doctor, ids):
    wanted = set(ids)
    rows = c.execute("""SELECT l.id,l.contact_id,l.revision,l.consent_at,ct.phone,ct.name,ct.opted_out,
        ct.experts_status,ct.experts_uuid FROM leads l JOIN contacts ct ON ct.id=l.contact_id
        WHERE l.doctor_id=? ORDER BY l.id""", (doctor,)).fetchall()
    values = [tuple(r) for r in rows if r["id"] in wanted]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def audience(store, payload, actor):
    doctor = payload.get("doctor_id")
    filters = parse_filters(payload.get("filters", {}))
    excluded = payload.get("exclude_ids", [])
    stages = payload.get("stage_ids", [])
    if any(not isinstance(x, list) or len(x)>5000 or any(not isinstance(k,str) for k in x) for x in (excluded,stages)):
        raise ValueError("Selecao de destinatarios invalida.")
    store.reconcile_patients()
    with store.db() as c:
        doc = store.row(c, "doctors", doctor)
        pipeline = payload.get("pipeline_id", "")
        if pipeline:
            store.owned(c, "pipelines", pipeline, doctor)
        for stage in stages:
            row = store.row(c,"stages",stage)
            owner = store.owned(c,"pipelines",row["pipeline_id"],doctor)
            if pipeline and owner["id"] != pipeline:
                raise ValueError("Fase de outro funil.")
        leads = [dict(r) for r in c.execute("""SELECT l.*,ct.name,ct.phone,ct.opted_out,ct.experts_uuid,ct.experts_status,s.kind
            FROM leads l JOIN contacts ct ON ct.id=l.contact_id JOIN stages s ON s.id=l.stage_id
            WHERE l.doctor_id=? AND (?='' OR l.pipeline_id=?) ORDER BY l.updated_at DESC,l.id""", (doctor,pipeline,pipeline))]
        if stages:
            leads = [l for l in leads if l["stage_id"] in stages]
        store.attach_customer_values(c,leads,doc["name"],bool(filters["search"]))
        if appointment_active(filters):
            store.attach_appointment_filters(leads,doc["name"],filters)
        coverage = len({l["contact_id"] for l in leads if not financial_ready(l,filters) or not appointment_ready(l,filters)})
        leads = ordered([l for l in leads if matches(l,filters)],filters)
        if len(leads)>5000:
            raise ValueError("A selecao ultrapassa 5000 oportunidades. Refine os filtros.")
        reasons=Counter();selected=[];contacts=set();duplicates=0
        for l in leads:
            reason = "Removido da selecao" if l["contact_id"] in excluded else "Descadastrado" if l["opted_out"] else "Sem autorizacao" if not l["consent_at"] else "Cadastro a conferir" if l["experts_status"] == "review" else ""
            if reason:
                reasons[reason]+=1
            elif l["contact_id"] in contacts:
                duplicates+=1
            else:
                contacts.add(l["contact_id"]);selected.append(l)
        ids=[l["id"] for l in selected]
        result={"selected":len(leads),"recipients":len(selected),"duplicates":duplicates,"excluded":sum(reasons.values()),
                "reasons":dict(reasons),"incomplete":coverage}
        key=uuid.uuid4().hex
        c.execute("DELETE FROM campaign_audiences WHERE expires_at<? AND campaign_id=''",(time.time()-86400,))
        c.execute("INSERT INTO campaign_audiences VALUES(?,?,?,?,?,?,?)",(key,doctor,str(actor["id"]),json.dumps({"ids":ids,"summary":result}),fingerprint(c,doctor,ids),time.time()+1800,""))
    return {"preview_id":key,**result,"sample":[{"id":l["id"],"contact_id":l["contact_id"],"name":l["name"],"phone":l["phone"]} for l in selected[:100]],"sample_limit":100}


def create(store,payload,actor):
    with store.db() as c:
        snapshot=store.row(c,"campaign_audiences",payload.get("preview_id"))
        if snapshot["doctor_id"] != payload.get("doctor_id") or snapshot["actor_id"] != str(actor["id"]):
            raise LookupError()
        data=json.loads(snapshot["payload"])
    return store.campaign({**payload,"lead_ids":data["ids"],"_audience_id":snapshot["id"]},actor["nome"],actor["id"])


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
        counts={r[0]:r[1] for r in c.execute("SELECT status,count(*) FROM campaigns WHERE doctor_id=? AND created_at>=? AND created_at<? GROUP BY status",(doctor,ts,te))}
        rows=[dict(r) for r in c.execute("""SELECT ca.*,ch.name channel_name,ch.phone channel_phone,ch.state channel_state,
            ch.daily_limit,ch.next_send_at FROM campaigns ca JOIN channels ch ON ch.id=ca.channel_id
            WHERE ca.doctor_id=? AND ca.created_at>=? AND ca.created_at<? AND (?='' OR ca.status=?)
            ORDER BY ca.created_at DESC,ca.id LIMIT 20 OFFSET ?""",(doctor,ts,te,status,status,(page-1)*20))]
        for row in rows:
            row["counts"]={r[0]:r[1] for r in c.execute("SELECT status,count(*) FROM messages WHERE campaign_id=? GROUP BY status",(row["id"],))}
            row["weekdays"]=json.loads(row["weekdays"])
            row["total"]=sum(row["counts"].values())
            row["progress"]=round(100*(row["total"]-row["counts"].get("queued",0)-row["counts"].get("sending",0))/row["total"]) if row["total"] else 0
            row["daily_used"]=c.execute("SELECT count(*) FROM messages WHERE channel_id=? AND campaign_id IS NOT NULL AND (sent_at>=? OR claimed_at>=?)",(row["channel_id"],datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp(),datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())).fetchone()[0]
        metrics={r[0]:r[1] for r in c.execute("""SELECT m.status,count(*) FROM messages m JOIN campaigns ca ON ca.id=m.campaign_id
            WHERE ca.doctor_id=? AND m.claimed_at>=? AND m.claimed_at<? GROUP BY m.status""",(doctor,ts,te))}
        sent=c.execute("SELECT m.sent_at FROM messages m JOIN campaigns ca ON ca.id=m.campaign_id WHERE ca.doctor_id=? AND m.status='sent' AND m.sent_at>=? AND m.sent_at<?",(doctor,ts,te)).fetchall()
        days=Counter(datetime.fromtimestamp(r[0],TZ).date().isoformat() for r in sent)
        dedup=c.execute("SELECT coalesce(sum(duplicates_count),0) FROM campaigns WHERE doctor_id=? AND created_at>=? AND created_at<?",(doctor,ts,te)).fetchone()[0]
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
        rows=[dict(r) for r in c.execute("SELECT m.id,m.status,m.created_at,m.sent_at,m.claimed_at,m.skip_reason,m.sender_name,coalesce(nullif(m.recipient_phone,''),ct.phone) phone,ct.name FROM messages m JOIN contacts ct ON ct.id=m.contact_id WHERE m.campaign_id=? ORDER BY m.created_at,m.id",(key,))]
    rows=[r for r in rows if (not status or r["status"]==status) and (not search or search in (r["name"]+" "+r["phone"]).casefold())]
    return {"campaign":campaign,"items":rows[(page-1)*50:page*50],"total":len(rows),"page":page,"has_more":page*50<len(rows)}
