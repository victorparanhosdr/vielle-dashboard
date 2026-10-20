"""Bounded Kanban pages and totals over the complete filtered funnel."""
import hashlib
import json
import time

from crm_store import Conflict

PAGE_SIZE = 20


def quotes(items):
    contacts = {l["contact_id"]: l for l in items}.values()
    cents = pending = 0
    for lead in contacts:
        history = lead.get("customer_history", {})
        amount = history.get("pending_quotes_cents")
        if history.get("quotes_state") == "ready" and type(amount) is int:
            cents += amount
        elif history.get("quotes_state") != "unlinked":
            pending += 1
    return {"cents": cents, "pending": pending}


def page(items, scope, cursor=""):
    snapshot = hashlib.sha256(json.dumps([scope, [(l["id"], l["revision"]) for l in items]], sort_keys=True).encode()).hexdigest()
    start = 0
    if cursor:
        if not isinstance(cursor, str) or len(cursor) > 512:
            raise ValueError("Pagina do Kanban invalida.")
        try:
            token = json.loads(cursor)
        except (ValueError, TypeError):
            raise ValueError("Pagina do Kanban invalida.") from None
        if not isinstance(token, list) or len(token) != 2 or any(not isinstance(v, str) for v in token):
            raise ValueError("Pagina do Kanban invalida.")
        if token[0] != snapshot:
            raise Conflict("Esta fase mudou. Atualize o funil para continuar.")
        index = next((i for i, lead in enumerate(items) if lead["id"] == token[1]), None)
        if index is None:
            raise ValueError("Pagina do Kanban invalida.")
        start = index + 1
    batch = items[start:start + PAGE_SIZE]
    return {"leads": batch, "total": len(items), "loaded": start + len(batch),
            "next_cursor": json.dumps([snapshot, batch[-1]["id"]], separators=(",", ":")) if start + len(batch) < len(items) else ""}


def overview(leads, pipeline, doctor, filters, today):
    items = [l for l in leads if pipeline and l["pipeline_id"] == pipeline["id"]]
    customers = list({l["contact_id"]: l for l in items}.values())
    tiers = {tier: sum(l.get("customer_value", {}).get("tier") == tier for l in customers) for tier in ("gold", "silver", "bronze")}
    active = [l for l in items if l["kind"] == "open"]
    columns, visible = [], []
    for stage in pipeline["stages"] if pipeline else []:
        members = [l for l in items if l["stage_id"] == stage["id"]]
        batch = page(members, [doctor, pipeline["id"], stage["id"], filters])
        visible.extend(batch.pop("leads"))
        columns.append({"stage_id": stage["id"], **batch, "quotes": quotes(members), "value_cents": sum(l.get("value_cents",0) for l in members)})
    return visible, {"pipeline_id": pipeline["id"] if pipeline else "", "columns": columns,
        "summary": {"total": len(items), "won": sum(l["kind"] == "won" for l in items), "quotes": quotes(customers),
                    "overdue": sum(bool(l["next_action"] and l["next_action"] < today) for l in active),
                    "stale": sum(time.time() - l["updated_at"] > 7 * 86400 for l in active),
                    "tiers": tiers, "unclassified": len(customers) - sum(tiers.values()),
                    "value_cents": sum(l.get("value_cents",0) for l in items),
                    "course": {"paid_students": sum(l.get("course_data", {}).get("paid_count", 0) > 0 for l in customers),
                               "paid_cents": sum(l.get("course_data", {}).get("paid_cents", 0) for l in customers),
                               "pending": sum(l.get("course_data", {}).get("pending_count", 0) for l in customers),
                               "refunds": sum(l.get("course_data", {}).get("refunded_count", 0) for l in customers)}}}
