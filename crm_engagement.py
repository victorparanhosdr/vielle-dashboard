"""Read-only sales journey and fresh-message signals, scoped to live CRM access."""
from collections import Counter
from datetime import datetime, timedelta
import time

from crm_reminders import permitted_pipelines
from crm_store import TZ, integer


def _personal_xp(c, user):
    ids = permitted_pipelines(c)
    marks = ",".join("?" for _ in ids) or "NULL"
    return c.execute("""SELECT coalesce(sum(x.points),0) FROM xp x JOIN leads l ON l.id=x.lead_id
        WHERE x.actor=? AND l.deleted_at=0 AND l.pipeline_id IN (""" + marks + ")", (user["nome"], *ids)).fetchone()[0]


def begin_feedback(store, user, ids):
    """Capture cursors before an explicit user mutation, not a periodic refresh."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return None
    with store.db() as c:
        for key in ids:
            store.row(c, "leads", key)
        return {"ids": ids, "events": c.execute("SELECT coalesce(max(id),0) FROM events").fetchone()[0],
                "xp": c.execute("SELECT coalesce(max(id),0) FROM xp").fetchone()[0], "personal": _personal_xp(c, user)}


def finish_feedback(store, user, marker):
    if marker is None:
        return None
    ids = marker["ids"]
    marks = ",".join("?" for _ in ids)
    with store.db() as c:
        for key in ids:
            store.row(c, "leads", key)
        stages = [dict(r) for r in c.execute("""SELECT e.id,s.kind FROM events e JOIN leads l ON l.id=e.lead_id
            JOIN stages s ON s.id=l.stage_id WHERE e.id>? AND e.actor=? AND e.kind='stage'
            AND e.lead_id IN (""" + marks + ") ORDER BY e.id", (marker["events"], user["nome"], *ids))]
        awards = [dict(r) for r in c.execute("SELECT id,label,points FROM xp WHERE id>? AND actor=? AND lead_id IN (" + marks + ") ORDER BY id",
                                           (marker["xp"], user["nome"], *ids))]
        if not stages and not awards:
            return None
        personal = _personal_xp(c, user)
    level = personal // 100 + 1
    kind = "level" if level > marker["personal"] // 100 + 1 else "win" if any(s["kind"] == "won" for s in stages) else "stage" if stages else "achievement"
    event = "stage:" + str(stages[-1]["id"]) if stages else "xp:" + str(awards[-1]["id"])
    return {"id": event, "actor_id": str(user["id"]), "kind": kind, "xp_ids": [p["id"] for p in awards], "points": sum(p["points"] for p in awards),
            "level": level, "stage_changes": len(stages)}


def snapshot(store, user, query):
    now = time.time()
    clock = datetime.fromtimestamp(now, TZ)
    day = clock.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    after_message = integer(query.get("messages_after", [0])[0], 0, 2**63 - 1)
    after_xp = integer(query.get("xp_after", [0])[0], 0, 2**63 - 1)
    watching = query.get("watch") == ["1"]
    with store.db() as c:
        ids = permitted_pipelines(c)
        marks = ",".join("?" for _ in ids) or "NULL"
        points = [dict(r) for r in c.execute("""SELECT x.* FROM xp x JOIN leads l ON l.id=x.lead_id
            WHERE l.deleted_at=0 AND l.pipeline_id IN (""" + marks + ") ORDER BY x.id", ids)]
        message_cursor = c.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]
        fresh = c.execute("""SELECT count(*) FROM messages m JOIN channels ch ON ch.id=m.channel_id
            WHERE m.rowid>? AND m.direction='in' AND m.created_at>=? AND ch.archived_at=0
            AND EXISTS(SELECT 1 FROM leads l WHERE l.contact_id=m.contact_id AND l.doctor_id=ch.doctor_id
                AND l.deleted_at=0 AND l.pipeline_id IN (""" + marks + "))", (after_message, now - 120, *ids)).fetchone()[0] if watching else 0
    own = [p for p in points if p["actor"] == user["nome"]]
    weekly = [p for p in points if p["created_at"] >= week.timestamp()]
    today = [p for p in own if p["created_at"] >= day.timestamp()]
    leaders = Counter()
    for p in weekly:
        leaders[p["actor"]] += p["points"]
    personal = sum(p["points"] for p in own)
    active_days = {datetime.fromtimestamp(p["created_at"], TZ).date() for p in own}
    cursor = clock.date() if clock.date() in active_days else clock.date() - timedelta(days=1)
    streak = 0
    while cursor in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    goals = [{"id": key, "label": label, "count": sum(p["label"] == source for p in today), "target": target, "icon": icon}
             for key, label, source, target, icon in (
                 ("followups", "Retomar conversas", "Follow-up registrado", 5, "message-circle"),
                 ("advances", "Avançar oportunidades", "Avanco no funil", 3, "route"),
                 ("wins", "Conquistar um negócio", "Fechamento", 1, "trophy"))]
    wins = sum(p["label"] == "Fechamento" for p in own)
    badges = [
        {"label": "Primeira conquista", "detail": "1 negócio ganho", "icon": "flag", "unlocked": wins >= 1},
        {"label": "Em movimento", "detail": "3 dias de sequência", "icon": "flame", "unlocked": streak >= 3},
        {"label": "Relacionamento", "detail": "25 follow-ups registrados", "icon": "heart-handshake", "unlocked": sum(p["label"] == "Follow-up registrado" for p in own) >= 25},
        {"label": "Dia completo", "detail": "Todas as missões de hoje", "icon": "badge-check", "unlocked": all(g["count"] >= g["target"] for g in goals)},
    ]
    celebrations = [{"id": p["id"], "actor": p["actor"], "label": p["label"], "points": p["points"]}
                    for p in points if watching and p["id"] > after_xp and p["created_at"] >= now - 120
                    and (p["actor"] == user["nome"] or p["label"] == "Fechamento")]
    return {"message_cursor": message_cursor, "xp_cursor": max((p["id"] for p in points), default=0),
            "fresh_messages": fresh, "celebrations": celebrations[-3:], "server_time": now,
            "journey": {"name": user["nome"], "xp": personal, "level": personal // 100 + 1, "progress": personal % 100,
                        "today_xp": sum(p["points"] for p in today), "week_xp": sum(p["points"] for p in weekly if p["actor"] == user["nome"]),
                        "team_week_xp": sum(p["points"] for p in weekly), "streak": streak, "goals": goals, "badges": badges,
                        "week_start": week.date().isoformat(), "team": [{"name": name, "xp": xp} for name, xp in leaders.most_common(20)]}}
