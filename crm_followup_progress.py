"""Read-only, scope-bound follow-up progress for cards, filters and lists."""
import json

from crm_access import pipeline_visible
from crm_clinic_journey import clinical


def progress(row):
    paused=bool(row["individual_paused"])
    result={"status":"paused" if paused else "none","has_cycle":bool(row["cycle_id"]),
        "accepted_steps":0,"completed_steps":0,"total_steps":0,"next_step":0,"next_at":0,
        "last_accepted_at":0,"pending_status":"","paused_individually":paused,"reason":""}
    if not row["cycle_id"]:return result
    try:
        steps=json.loads(row["cycle_config"])["steps"]
        if not isinstance(steps,list) or not 1<=len(steps)<=20:raise ValueError()
    except (ValueError,TypeError,KeyError):
        result.update(status="review",reason="Configuração do acompanhamento precisa de conferência")
        return result
    total=len(steps);accepted=min(total,max(0,int(row["accepted_steps"])));completed=min(total,accepted+int(row["completed_bots"]))
    status=row["cycle_status"] if row["cycle_status"] in {"waiting","paused","cancelled","completed"} else "review"
    reason=row["cycle_reason"]
    if row["latest_inbound"]>row["inbound_seq"]:
        status="responded";reason="Cliente respondeu; ciclo anterior encerrado"
    elif row["cycle_stage"]!=row["stage_id"]:
        status="cancelled";reason="Etapa alterada; acompanhamento anterior encerrado"
    elif row["pending_status"]=="unknown" or row["bot_unknown"]:
        status="review";reason="Envio sem confirmação; não repetir automaticamente"
    elif row["bot_status"] in {"failed","cancelled"} and status=="waiting":
        status="review";reason="Robô interrompido; conferir execução"
    elif paused:
        status="paused";reason="Pausado na ficha do contato"
    elif status=="waiting" and not row["policy_enabled"]:
        status="paused";reason="Régua desligada"
    elif status=="waiting" and row["policy_revision"]!=row["current_policy_revision"]:
        status="cancelled";reason="Régua alterada"
    next_step=(row["attempt"] if row["pending_status"] in {"queued","sending","unknown"} or row["bot_run_id"] else row["attempt"]+1) if status in {"waiting","paused","review"} else 0
    result.update(status=status,accepted_steps=accepted,completed_steps=completed,total_steps=total,
        next_step=min(total,next_step),next_at=row["due_at"] if status=="waiting" else 0,
        last_accepted_at=row["last_accepted_at"] or 0,pending_status=row["pending_status"] or ("robot" if row["bot_run_id"] else ""),reason=reason)
    return result


def attach(c,items):
    by_id={}
    for item in items:
        item.pop("followup_progress",None)
        key=item["lead_id"] if "lead_id" in item else item.get("id")
        if key:by_id.setdefault(key,[]).append(item)
    ids=list(by_id)
    for start in range(0,len(ids),800):
        batch=ids[start:start+800]
        rows=c.execute(f"""SELECT l.id,l.doctor_id,l.pipeline_id,l.stage_id,p.name,p.course_key,p.institute_key,
            coalesce(pref.paused,0) individual_paused,f.id cycle_id,f.config cycle_config,
            f.status cycle_status,f.reason cycle_reason,f.stage_id cycle_stage,f.inbound_seq,f.attempt,f.due_at,
            f.policy_revision,coalesce(policy.enabled,0) policy_enabled,policy.revision current_policy_revision,
            pending.status pending_status,f.bot_run_id,robot.status bot_status,
            (SELECT count(*) FROM crm_followup_bot_steps b JOIN bot_runs r ON r.id=b.run_id WHERE b.cycle_id=f.id AND r.status='done') completed_bots,
            EXISTS(SELECT 1 FROM messages m JOIN crm_followup_bot_steps b ON b.run_id=m.bot_run_id WHERE b.cycle_id=f.id AND m.bot_run_id<>'' AND m.status='unknown') bot_unknown,
            coalesce((SELECT count(*) FROM messages m WHERE m.followup_cycle_id=f.id
                AND m.followup_cycle_id<>'' AND m.status='sent'),0) accepted_steps,
            (SELECT max(m.sent_at) FROM messages m WHERE m.followup_cycle_id=f.id
                AND m.followup_cycle_id<>'' AND m.status='sent') last_accepted_at,
            coalesce((SELECT max(m.rowid) FROM messages m WHERE m.contact_id=l.contact_id AND m.direction='in'),0) latest_inbound
            FROM leads l JOIN pipelines p ON p.id=l.pipeline_id
            LEFT JOIN crm_followup_contact_preferences pref ON pref.contact_id=l.contact_id
            LEFT JOIN crm_followup_cycles f ON f.id=(SELECT latest.id FROM crm_followup_cycles latest
                WHERE latest.lead_id=l.id AND latest.doctor_id=l.doctor_id AND latest.pipeline_id=l.pipeline_id
                ORDER BY latest.created_at DESC,latest.rowid DESC LIMIT 1)
            LEFT JOIN followup_policies policy ON policy.pipeline_id=l.pipeline_id
            LEFT JOIN messages pending ON pending.id=f.message_id
            LEFT JOIN bot_runs robot ON robot.id=f.bot_run_id
            WHERE l.deleted_at=0 AND l.id IN ({','.join('?' for _ in batch)})""",batch).fetchall()
        for row in rows:
            row=dict(row)
            if not pipeline_visible(row) or not clinical(row):continue
            value=progress(row)
            for item in by_id[row["id"]]:
                if item.get("doctor_id",row["doctor_id"])!=row["doctor_id"] or item.get("pipeline_id",row["pipeline_id"])!=row["pipeline_id"]:continue
                item["followup_progress"]=value.copy()
