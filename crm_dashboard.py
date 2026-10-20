"""Read-only, doctor-scoped operational metrics. No provider requests or clinical writes."""
from collections import defaultdict
from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo

from crm_metrics import THREADS_CTE
from crm_access import contact_visible, pipeline_visible

TZ = ZoneInfo("America/Sao_Paulo")


def dashboard(store, doctor, days=7, *, include_operators=False, now=None):
    if str(days) not in {"7", "30", "90"}:
        raise ValueError("Periodo invalido.")
    days = int(days)
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, TZ).date()
    first = today - timedelta(days=days - 1)
    start = datetime.combine(first, datetime.min.time(), TZ).timestamp()
    daily = {(first + timedelta(days=i)).isoformat(): {"received": 0, "replies": 0} for i in range(days)}
    with store.db() as c:
        store.row(c, "doctors", doctor)
        pending = [r for r in c.execute(THREADS_CTE + "SELECT contact_id,waiting_since FROM threads WHERE unread>0", (doctor, "", "")) if contact_visible(c, r["contact_id"], doctor)]
        clients = {}
        for row in pending:
            clients[row["contact_id"]] = min(clients.get(row["contact_id"], now), row["waiting_since"] or now)
        waits = [max(0, int(now - stamp)) for stamp in clients.values()]
        buckets = [sum(a <= wait < b for wait in waits) for a, b in ((0, 900), (900, 3600), (3600, 86400), (86400, float("inf")))]
        hours = c.execute("""SELECT m.contact_id,cast(coalesce(nullif(m.provider_timestamp,0),m.created_at)/3600 AS INTEGER) hour,count(*) total
            FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE ch.doctor_id=? AND m.direction='in'
            AND coalesce(nullif(m.provider_timestamp,0),m.created_at)>=?
            AND coalesce(nullif(m.provider_timestamp,0),m.created_at)<=? GROUP BY m.contact_id,hour""", (doctor, start, now)).fetchall()
        for row in hours:
            if not contact_visible(c, row["contact_id"], doctor):
                continue
            key = datetime.fromtimestamp(row["hour"] * 3600, TZ).date().isoformat()
            if key in daily:
                daily[key]["received"] += row["total"]
        # One accepted manual reply, not signature preambles, campaign volume, or external sends.
        replies = [r for r in c.execute("""SELECT DISTINCT m.id,r.user_id,m.sender_name,m.contact_id,m.sent_at
            FROM reply_requests r JOIN messages m ON m.id=r.message_id JOIN channels ch ON ch.id=m.channel_id
            WHERE ch.doctor_id=? AND m.direction='out' AND m.status='sent' AND m.campaign_id IS NULL
            AND m.sent_at>=? AND m.sent_at<=?""", (doctor, start, now)) if contact_visible(c, r["contact_id"], doctor)]
        operators = defaultdict(lambda: {"name": "Operador", "replies": 0, "contacts": set(), "last_reply_at": 0})
        for row in replies:
            key = datetime.fromtimestamp(row["sent_at"], TZ).date().isoformat()
            if key in daily:
                daily[key]["replies"] += 1
            op = operators[str(row["user_id"])]
            op["name"] = row["sender_name"] or op["name"]
            op["replies"] += 1
            op["contacts"].add(row["contact_id"])
            op["last_reply_at"] = max(op["last_reply_at"], row["sent_at"])
        funnels = [dict(r) for r in c.execute("""SELECT p.id,p.name,p.doctor_id,p.institute_key,count(l.id) total,
            sum(CASE WHEN s.kind='won' THEN 1 ELSE 0 END) won,
            sum(CASE WHEN s.kind='lost' THEN 1 ELSE 0 END) lost
            FROM pipelines p LEFT JOIN leads l ON l.pipeline_id=p.id AND l.deleted_at=0 LEFT JOIN stages s ON s.id=l.stage_id
            WHERE p.doctor_id=? GROUP BY p.id ORDER BY p.name""", (doctor,)) if pipeline_visible(dict(r))]
        overdue = sum(r[0] in {p["id"] for p in funnels} for r in c.execute("""SELECT l.pipeline_id FROM leads l JOIN stages s ON s.id=l.stage_id
            WHERE l.doctor_id=? AND l.deleted_at=0 AND s.kind='open' AND l.next_action<>'' AND l.next_action<?""", (doctor, today.isoformat())))
        channels = c.execute("SELECT count(*),coalesce(sum(state='open'),0) FROM channels WHERE doctor_id=?", (doctor,)).fetchone()
    return {"server_time": now, "days": days, "from": first.isoformat(), "to": today.isoformat(),
            "pending_clients": len(clients), "pending_conversations": len(pending), "waiting_buckets": buckets,
            "longest_wait_seconds": max(waits, default=0), "average_wait_seconds": int(sum(waits) / len(waits)) if waits else 0,
            "manual_replies": len(replies), "replied_clients": len({r["contact_id"] for r in replies}),
            "received_messages": sum(day["received"] for day in daily.values()), "overdue_actions": overdue,
            "channels": {"total": channels[0], "connected": channels[1]}, "funnels": funnels,
            "daily": [{"date": key, **value} for key, value in daily.items()],
            "operators": sorted([{"id": key, "name": value["name"], "replies": value["replies"],
                "contacts": len(value["contacts"]), "last_reply_at": value["last_reply_at"]} for key, value in operators.items()],
                key=lambda row: (-row["replies"], row["name"])) if include_operators else None}
