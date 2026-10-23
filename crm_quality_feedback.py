"""Published employee reviews, acknowledgements and a single audited appeal."""
from datetime import datetime
import json
import re
import time

from crm_appointments import TZ
from crm_store import Conflict, integer


STATES = {"awaiting_ack", "accepted", "contested", "resolved"}
PUBLISHED = "COALESCE(NULLIF(a.published_at,0),(SELECT min(r.created_at) FROM quality_reviews r WHERE r.assessment_id=a.id),NULLIF(j.finished_at,0),j.created_at)"


def schema(c):
    if "published_at" not in {r[1] for r in c.execute("PRAGMA table_info(quality_assessments)")}:
        c.execute("ALTER TABLE quality_assessments ADD COLUMN published_at REAL NOT NULL DEFAULT 0")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS quality_responses(
            assessment_id TEXT PRIMARY KEY REFERENCES quality_assessments(id),
            revision INTEGER NOT NULL DEFAULT 0,
            accepted_revision INTEGER NOT NULL DEFAULT 0,accepted_at REAL NOT NULL DEFAULT 0,
            contest_used INTEGER NOT NULL DEFAULT 0,contest_revision INTEGER NOT NULL DEFAULT 0,
            contest_reason TEXT NOT NULL DEFAULT '',contested_at REAL NOT NULL DEFAULT 0,
            decision TEXT NOT NULL DEFAULT '',decision_reason TEXT NOT NULL DEFAULT '',
            decided_by TEXT NOT NULL DEFAULT '',decided_at REAL NOT NULL DEFAULT 0,
            decided_revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS quality_response_events(
            id INTEGER PRIMARY KEY,assessment_id TEXT NOT NULL REFERENCES quality_assessments(id),
            kind TEXT NOT NULL,actor_id TEXT NOT NULL,created_at REAL NOT NULL,
            grade_revision INTEGER NOT NULL,body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS quality_subject ON quality_assessments(actor_id,reviewed,published_at);
        CREATE INDEX IF NOT EXISTS quality_response_history ON quality_response_events(assessment_id,id);
    """)


def response(c, assessment):
    row = c.execute("SELECT * FROM quality_responses WHERE assessment_id=?", (assessment["id"],)).fetchone()
    data = dict(row) if row else {"revision": 0, "accepted_revision": 0, "accepted_at": 0,
        "contest_used": 0, "contest_revision": 0, "contest_reason": "", "contested_at": 0,
        "decision": "", "decision_reason": "", "decided_at": 0, "decided_revision": 0}
    data.pop("assessment_id", None)
    data.pop("decided_by", None)
    current = assessment["revision"]
    data["status"] = ("unpublished" if not assessment["reviewed"] else
        "contested" if data["contest_used"] and not data["decision"] else
        "accepted" if data["accepted_revision"] == current else
        "resolved" if data["contest_used"] else "awaiting_ack")
    data["can_accept"] = data["status"] in {"awaiting_ack", "resolved"}
    data["can_contest"] = data["status"] == "awaiting_ack" and not data["contest_used"]
    return data


def event(c, ident, kind, actor, revision, body, now):
    c.execute("INSERT INTO quality_response_events(assessment_id,kind,actor_id,created_at,grade_revision,body) VALUES(?,?,?,?,?,?)",
        (ident, kind, str(actor), now, revision, json.dumps(body)))


def owned(c, store, doctor, ident, actor):
    if not isinstance(ident, str) or not ident or len(ident) > 128:
        raise ValueError("Avaliação inválida.")
    row = c.execute("SELECT a.*,j.doctor_id,j.channel_id,j.contact_id,j.snapshot,ct.name contact_name,ch.name channel_name," + PUBLISHED + " received_at FROM quality_assessments a JOIN quality_jobs j ON j.id=a.job_id JOIN contacts ct ON ct.id=j.contact_id JOIN channels ch ON ch.id=j.channel_id WHERE a.id=? AND a.actor_id=? AND a.reviewed=1 AND j.doctor_id=?",
        (ident, str(actor), doctor)).fetchone()
    if not row:
        raise LookupError()
    store.conversation_contact(c, doctor, row["channel_id"], row["contact_id"])
    return row


def item(c, row, full=False):
    data = {key: row[key] for key in ("id", "score", "revision", "received_at", "contact_name", "channel_name")}
    data["response"] = response(c, row)
    snap = json.loads(row["snapshot"])
    data["period"] = {"from": snap.get("from", 0), "to": snap.get("to", 0)}
    if full:
        result = json.loads(row["result"])
        from crm_quality import CATALOG
        data["result"] = {"summary": result.get("summary", ""), "feedback": result.get("feedback", ""),
            "items": [{"id": i["id"], "label": CATALOG.get(i["id"], {}).get("label", i["id"]),
                "penalty": CATALOG.get(i["id"], {}).get("penalty", 0), "outcome": i["outcome"], "reason": i["reason"]}
                for i in result["items"]]}
        # Exclude other staff grades, raw snapshots, contact details and provisional results.
        data["reviews"] = [{"created_at": r["created_at"], "reason": r["reason"],
            "score": json.loads(r["result"]).get("score")} for r in c.execute(
                "SELECT created_at,reason,result FROM quality_reviews WHERE assessment_id=? ORDER BY id", (row["id"],))]
        data["events"] = [{"kind": r["kind"], "created_at": r["created_at"],
            "grade_revision": r["grade_revision"], **json.loads(r["body"])} for r in c.execute(
                "SELECT * FROM quality_response_events WHERE assessment_id=? ORDER BY id", (row["id"],))]
    return data


def month_bounds(value):
    value = value or datetime.now(TZ).strftime("%Y-%m")
    if not isinstance(value, str) or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value):
        raise ValueError("Mês inválido.")
    start = datetime.strptime(value, "%Y-%m").replace(tzinfo=TZ)
    end = start.replace(year=start.year+1, month=1) if start.month == 12 else start.replace(month=start.month+1)
    return value, start.timestamp(), end.timestamp()


def listing(store, doctor, actor, *, month="", status="", page=1):
    month, start, end = month_bounds(month)
    page = integer(page, 1, 100000)
    if status and status not in STATES:
        raise ValueError("Situação inválida.")
    with store.db() as c:
        store.row(c, "doctors", doctor)
        rows = c.execute("SELECT a.id,a.score,a.revision,a.reviewed,j.channel_id,j.contact_id,json_object('from',json_extract(j.snapshot,'$.from'),'to',json_extract(j.snapshot,'$.to')) snapshot,ct.name contact_name,ch.name channel_name," + PUBLISHED + " received_at FROM quality_assessments a JOIN quality_jobs j ON j.id=a.job_id JOIN contacts ct ON ct.id=j.contact_id JOIN channels ch ON ch.id=j.channel_id WHERE a.actor_id=? AND a.reviewed=1 AND j.doctor_id=? AND " + PUBLISHED + ">=? AND " + PUBLISHED + "<? ORDER BY received_at DESC,a.id",
            (str(actor), doctor, start, end)).fetchall()
        visible = []
        for row in rows:
            try:
                store.conversation_contact(c, doctor, row["channel_id"], row["contact_id"])
            except (PermissionError, LookupError, ValueError):
                continue
            value = item(c, row)
            if not status or value["response"]["status"] == status:
                visible.append(value)
        scores = [v["score"] for v in visible if v["score"] is not None]
        pages = max(1, (len(visible)+24)//25)
        page = min(page, pages)
        return {"month": month, "status": status, "page": page, "pages": pages, "total": len(visible),
            "items": visible[(page-1)*25:page*25], "summary": {
                "average": sum(scores)/len(scores) if scores else None, "scored": len(scores),
                "pending": sum(v["response"]["status"] in {"awaiting_ack", "resolved"} for v in visible),
                "contested": sum(v["response"]["status"] == "contested" for v in visible)}}


def detail(store, doctor, ident, actor):
    with store.db() as c:
        return item(c, owned(c, store, doctor, ident, actor), full=True)


def respond(store, doctor, ident, payload, actor):
    action = payload.get("action")
    if action not in {"accept", "contest"}:
        raise ValueError("Resposta inválida.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = owned(c, store, doctor, ident, actor)
        if integer(payload.get("revision"), 1, 2**31-1) != row["revision"]:
            raise Conflict("A nota mudou. Leia a avaliação atual antes de responder.")
        old = response(c, row)
        if action == "accept" and old["status"] == "accepted":
            return old
        if integer(payload.get("response_revision"), 0, 2**31-1) != old["revision"]:
            raise Conflict("A resposta mudou. Atualize a avaliação.")
        if not old["can_accept"] or action == "contest" and not old["can_contest"]:
            raise Conflict("Esta avaliação já recebeu uma resposta. Só é permitida uma ressalva por avaliação.")
        c.execute("INSERT OR IGNORE INTO quality_responses(assessment_id) VALUES(?)", (ident,))
        now = time.time()
        if action == "accept":
            c.execute("UPDATE quality_responses SET accepted_revision=?,accepted_at=?,revision=revision+1 WHERE assessment_id=?",
                (row["revision"], now, ident))
            event(c, ident, "accepted", actor, row["revision"], {"score": row["score"]}, now)
        else:
            from crm_quality import clean
            reason = clean(payload.get("reason", ""), 2000)
            if not reason:
                raise ValueError("Descreva a ressalva para o líder.")
            c.execute("UPDATE quality_responses SET contest_used=1,contest_revision=?,contest_reason=?,contested_at=?,revision=revision+1 WHERE assessment_id=?",
                (row["revision"], reason, now, ident))
            event(c, ident, "contested", actor, row["revision"], {"reason": reason, "score": row["score"]}, now)
        return response(c, row)


def review_response(c, row, payload, actor, result, now):
    """Called in the same transaction as the leader's grade revision."""
    old = response(c, row)
    decision = payload.get("contest_decision", "")
    if old["status"] != "contested":
        if decision:
            raise Conflict("Não há ressalva pendente nesta avaliação.")
        return
    if integer(payload.get("response_revision"), 0, 2**31-1) != old["revision"]:
        raise Conflict("A ressalva mudou. Atualize antes de responder.")
    if decision not in {"accept", "reject"}:
        raise ValueError("Informe a decisão sobre a ressalva.")
    from crm_quality import clean
    reason = clean(payload.get("contest_note", ""), 2000)
    if not reason:
        raise ValueError("Registre a resposta do líder à ressalva.")
    previous = json.loads(row["result"])
    before = {i["id"] for i in previous["items"] if i["outcome"] == "fail"}
    after = {i["id"] for i in result["items"] if i["outcome"] == "fail"}
    if decision == "reject" and before != after:
        raise ValueError("Para ajustar os descontos, acolha a ressalva.")
    c.execute("UPDATE quality_responses SET decision=?,decision_reason=?,decided_by=?,decided_at=?,decided_revision=?,revision=revision+1 WHERE assessment_id=?",
        (decision, reason, str(actor), now, row["revision"]+1, row["id"]))
    event(c, row["id"], "resolved", actor, row["revision"]+1,
        {"decision": decision, "reason": reason, "previous_score": row["score"], "score": result["score"]}, now)
