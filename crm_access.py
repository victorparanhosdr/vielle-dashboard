"""Request-local institute access for the shared CRM contact store."""
from contextlib import contextmanager
from contextvars import ContextVar
import json

INSTITUTES = ContextVar("crm_institutes", default=None)


@contextmanager
def institute_access(keys):
    token = INSTITUTES.set(frozenset(keys))
    try:
        yield
    finally:
        INSTITUTES.reset(token)


def pipeline_visible(row):
    allowed = INSTITUTES.get()
    return not row.get("institute_key") or allowed is None or row["institute_key"] in allowed


def contact_visible(c, contact):
    allowed = INSTITUTES.get()
    if allowed is None:
        return True
    rows = [dict(r) for r in c.execute("SELECT p.institute_key FROM leads l JOIN pipelines p ON p.id=l.pipeline_id WHERE l.contact_id=?", (contact,))]
    return not rows or any(pipeline_visible(row) for row in rows)


def check_row(c, table, row):
    if INSTITUTES.get() is None:
        return
    if table == "pipelines":
        permitted = pipeline_visible(row)
    elif table in {"leads", "stages"}:
        pipeline = c.execute("SELECT institute_key FROM pipelines WHERE id=?", (row["pipeline_id"],)).fetchone()
        permitted = pipeline is not None and pipeline_visible(dict(pipeline))
    elif table == "contacts":
        permitted = contact_visible(c, row["id"])
    elif "contact_id" in row:
        permitted = contact_visible(c, row["contact_id"])
    elif table == "campaigns":
        permitted = set(json.loads(row.get("institute_keys", "[]"))).issubset(INSTITUTES.get()) and all(contact_visible(c, r[0]) for r in c.execute("SELECT DISTINCT contact_id FROM messages WHERE campaign_id=?", (row["id"],)))
    else:
        permitted = True
    if not permitted:
        raise PermissionError("Seu usuario nao possui acesso ao instituto deste curso.")
