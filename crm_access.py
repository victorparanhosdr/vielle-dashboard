"""Request-local institute access for the shared CRM contact store."""
from contextlib import contextmanager
from contextvars import ContextVar
import json

INSTITUTES = ContextVar("crm_institutes", default=None)
CRM_SCOPE = ContextVar("crm_user_scope", default=None)
CURRENT_DOCTOR = ContextVar("crm_request_doctor", default="")


@contextmanager
def crm_access(scope, doctor=""):
    token = CRM_SCOPE.set(scope)
    current = CURRENT_DOCTOR.set(doctor)
    try:
        if doctor and not doctor_visible(doctor):
            raise PermissionError("Seu usuario nao possui acesso a este profissional no CRM.")
        yield
    finally:
        CURRENT_DOCTOR.reset(current)
        CRM_SCOPE.reset(token)


def doctor_visible(doctor):
    scope = CRM_SCOPE.get()
    return scope is None or doctor in scope


def all_pipelines(doctor):
    scope = CRM_SCOPE.get()
    return scope is None or doctor in scope and scope[doctor] is None


@contextmanager
def institute_access(keys):
    token = INSTITUTES.set(frozenset(keys))
    try:
        yield
    finally:
        INSTITUTES.reset(token)


def activity_visible(row):
    allowed = INSTITUTES.get()
    return doctor_visible(row.get("doctor_id", "")) and (not row.get("institute_key") or allowed is None or row["institute_key"] in allowed)


def pipeline_visible(row):
    scope = CRM_SCOPE.get()
    if not activity_visible(row):
        return False
    return scope is None or scope.get(row.get("doctor_id", "")) is None or (row.get("pipeline_id") or row.get("id")) in scope[row["doctor_id"]]


def form_visible(row):
    if not activity_visible(row):
        return False
    if all_pipelines(row["doctor_id"]):
        return True
    # A shared/unbound form can contain answers from outside a limited funnel.
    versions = [json.loads(row[k]) for k in ("draft", "published") if row.get(k)]
    return bool(versions) and all(pipeline_visible({"doctor_id": row["doctor_id"],
        "pipeline_id": v.get("crm", {}).get("pipeline_id", ""), "institute_key": row.get("institute_key", "")}) for v in versions)


def contact_visible(c, contact, doctor=""):
    allowed = INSTITUTES.get()
    scope = CRM_SCOPE.get()
    if allowed is None and scope is None:
        return True
    doctor = doctor or CURRENT_DOCTOR.get()
    rows = [dict(r) for r in c.execute("SELECT p.* FROM leads l JOIN pipelines p ON p.id=l.pipeline_id WHERE l.contact_id=? AND l.deleted_at=0 AND (?='' OR l.doctor_id=?)", (contact, doctor, doctor))]
    if any(pipeline_visible(row) for row in rows):
        return True
    # A permitted source funnel retains its own conversation history after a handoff.
    origins = c.execute("""SELECT p.* FROM lead_transfers t JOIN pipelines p ON p.id=t.from_pipeline_id
        WHERE t.contact_id=? AND t.from_doctor_id=?""", (contact, doctor)).fetchall()
    if any(pipeline_visible(dict(row)) for row in origins):
        return True
    if rows:
        return False
    if scope is None:
        return True
    return bool(doctor and all_pipelines(doctor) and c.execute("SELECT 1 FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.contact_id=? AND ch.doctor_id=? LIMIT 1", (contact, doctor)).fetchone())


def check_row(c, table, row):
    if INSTITUTES.get() is None and CRM_SCOPE.get() is None:
        return
    permitted = doctor_visible(row.get("doctor_id", "")) if "doctor_id" in row else True
    if table == "doctors":
        permitted = doctor_visible(row["id"])
    elif table == "pipelines":
        permitted = pipeline_visible(row)
    elif "pipeline_id" in row:
        pipeline = c.execute("SELECT * FROM pipelines WHERE id=?", (row["pipeline_id"],)).fetchone()
        permitted = permitted and pipeline is not None and pipeline_visible(dict(pipeline))
    elif "lead_id" in row:
        pipeline = c.execute("SELECT p.* FROM leads l JOIN pipelines p ON p.id=l.pipeline_id WHERE l.id=?", (row["lead_id"],)).fetchone()
        permitted = permitted and pipeline is not None and pipeline_visible(dict(pipeline))
    elif table == "contacts":
        permitted = contact_visible(c, row["id"])
    elif table == "campaign_audiences":
        ids = set(json.loads(row["payload"]).get("ids", []))
        visible = {r["id"] for r in c.execute("SELECT l.id,p.id pipeline_id,p.doctor_id,p.institute_key FROM leads l JOIN pipelines p ON p.id=l.pipeline_id WHERE l.doctor_id=? AND l.deleted_at=0", (row["doctor_id"],)) if pipeline_visible(dict(r))}
        permitted = permitted and ids.issubset(visible)
    elif table == "campaigns":
        institutes = INSTITUTES.get()
        permitted = permitted and (institutes is None or set(json.loads(row.get("institute_keys", "[]"))).issubset(institutes))
        permitted = permitted and all(contact_visible(c, r[0], row["doctor_id"]) for r in c.execute("SELECT DISTINCT contact_id FROM messages WHERE campaign_id=?", (row["id"],)))
        if not all_pipelines(row["doctor_id"]):
            snapshot = c.execute("SELECT * FROM campaign_audiences WHERE campaign_id=?", (row["id"],)).fetchone()
            if not snapshot:
                permitted = False
            else:
                check_row(c, "campaign_audiences", dict(snapshot))
    elif "contact_id" in row:
        permitted = permitted and contact_visible(c, row["contact_id"], row.get("doctor_id", ""))
    if not permitted:
        raise PermissionError("Seu usuario nao possui acesso a este profissional, funil ou instituto no CRM.")
