"""Persistent, permission-scoped CRM reminders. Due alerts are derived from server time."""
from datetime import datetime
import json
import time
import uuid

from crm_access import crm_access, institute_access, pipeline_visible
from crm_store import Conflict, TZ, integer, text
from task_api import read_body


def schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS crm_reminders(
        id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),
        title TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',due_at REAL NOT NULL,
        assignee_id TEXT NOT NULL,assignee_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','done','cancelled')),
        created_by TEXT NOT NULL,request_id TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
        completed_at REAL NOT NULL DEFAULT 0,acknowledged_at REAL NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1,UNIQUE(created_by,request_id));
    CREATE INDEX IF NOT EXISTS crm_reminders_due ON crm_reminders(assignee_id,status,due_at);
    CREATE INDEX IF NOT EXISTS crm_reminders_lead ON crm_reminders(lead_id,status,due_at);
    """)


def local_due(value):
    if not isinstance(value, str) or len(value) != 16:
        raise ValueError("Informe a data e o horario do lembrete.")
    try:
        due = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
    except ValueError:
        raise ValueError("Data ou horario invalido.") from None
    if not 2020 <= due.year <= datetime.now(TZ).year + 10:
        raise ValueError("Data fora do intervalo permitido.")
    return due.timestamp()


def permitted_pipelines(c):
    return [r["id"] for r in c.execute("SELECT * FROM pipelines") if pipeline_visible(dict(r))]


def assignee_allowed(store, auth, user_id, lead):
    user = auth.get_public_user(user_id)
    if not user or user["status"] != "active" or "crm.view" not in user["permissions"].get("vielle", []):
        return None
    keys = store.course_source.store.allowed(user) if store.course_source else []
    with crm_access(None if user["is_master"] else user.get("crm_scope")), institute_access(keys), store.db() as c:
        pipeline = c.execute("SELECT * FROM pipelines WHERE id=?", (lead["pipeline_id"],)).fetchone()
        return user if pipeline and pipeline_visible(dict(pipeline)) else None


def assignees(store, auth, lead):
    result, page = [], 1
    while True:
        data = auth.list_users(status="active", page=page, page_size=100)
        for user in data["users"]:
            if assignee_allowed(store, auth, user["id"], lead):
                result.append({"id": str(user["id"]), "name": user["nome"]})
        if page * 100 >= data["total"]:
            return result
        page += 1


SELECT = """SELECT r.*,ct.name client_name,l.doctor_id,l.pipeline_id,l.stage_id,l.contact_id,
    p.name pipeline_name,d.name doctor_name,s.name stage_name
    FROM crm_reminders r JOIN leads l ON l.id=r.lead_id JOIN contacts ct ON ct.id=l.contact_id
    JOIN pipelines p ON p.id=l.pipeline_id JOIN doctors d ON d.id=l.doctor_id
    JOIN stages s ON s.id=l.stage_id"""


def serialize(row, now):
    item = dict(row)
    due = datetime.fromtimestamp(item["due_at"], TZ)
    item.update(due_local=due.strftime("%Y-%m-%dT%H:%M"), overdue=item["status"] == "pending" and item["due_at"] <= now)
    item.pop("request_id", None)
    return item


def listing(store, user, query, *, alerts=False):
    now = time.time()
    get = lambda key, default="": query.get(key, [default])[0]
    with store.db() as c:
        ids = permitted_pipelines(c)
        where, args = ["l.deleted_at=0", "l.pipeline_id IN (" + ",".join("?" for _ in ids) + ")"], list(ids)
        if not ids:
            return {"items": [], "total": 0, "counts": {}, "server_time": now, "page": 1}
        if alerts or get("owner", "mine") == "mine":
            where.append("r.assignee_id=?")
            args.append(str(user["id"]))
        elif get("owner") != "team":
            raise ValueError("Responsavel invalido.")
        for key, column in (("doctor", "l.doctor_id"), ("pipeline", "l.pipeline_id"), ("lead", "l.id")):
            if get(key):
                where.append(column + "=?")
                args.append(get(key))
        day = datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        today = day.timestamp()
        from datetime import timedelta
        tomorrow = (day + timedelta(days=1)).timestamp()
        base = " AND ".join(where)
        counts = dict(c.execute("""SELECT
            coalesce(sum(r.status='pending'),0) pending,
            coalesce(sum(r.status='pending' AND r.due_at<=?),0) overdue,
            coalesce(sum(r.status='pending' AND r.due_at>=? AND r.due_at<?),0) today,
            coalesce(sum(r.status='done'),0) done
            FROM crm_reminders r JOIN leads l ON l.id=r.lead_id WHERE """ + base, (now, today, tomorrow, *args)).fetchone())
        if alerts:
            where += ["r.status='pending'", "r.due_at<=?", "r.acknowledged_at=0"]
            args.append(now)
        else:
            status = get("status", "pending")
            if status not in {"pending", "done", "cancelled", "all", "today", "overdue", "upcoming"}:
                raise ValueError("Filtro de tarefas invalido.")
            if status in {"today", "overdue", "upcoming"}:
                where.append("r.status='pending'")
                if status == "today":
                    where.append("r.due_at>=? AND r.due_at<?")
                    args += [today, tomorrow]
                else:
                    where.append("r.due_at" + ("<=?" if status == "overdue" else ">?"))
                    args.append(now)
            elif status != "all":
                where.append("r.status=?")
                args.append(status)
            if get("search"):
                where.append("(r.title LIKE ? ESCAPE '\\' OR ct.name LIKE ? ESCAPE '\\')")
                pattern = "%" + text(get("search"), 160).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                args += [pattern, pattern]
        clause = " AND ".join(where)
        total = c.execute("SELECT count(*) FROM (" + SELECT + " WHERE " + clause + ")", args).fetchone()[0]
        page = 1 if alerts else integer(get("page", 1), 1, 1000000)
        page = min(page, max(1, (total + 39) // 40))
        order = "r.due_at DESC,r.id" if alerts else "r.due_at,r.id"
        rows = c.execute(SELECT + " WHERE " + clause + " ORDER BY " + order + " LIMIT 40 OFFSET ?", (*args, (page - 1) * 40)).fetchall()
        return {"items": [serialize(row, now) for row in rows], "total": total, "counts": counts, "page": page, "server_time": now}


def lead_choices(store, query):
    search = text(query.get("search", [""])[0], 160, required=False)
    with store.db() as c:
        ids = permitted_pipelines(c)
        if not ids:
            return []
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return [dict(r) for r in c.execute("""SELECT l.id,l.doctor_id,l.pipeline_id,ct.name,p.name pipeline_name,d.name doctor_name
            FROM leads l JOIN contacts ct ON ct.id=l.contact_id JOIN pipelines p ON p.id=l.pipeline_id
            JOIN doctors d ON d.id=l.doctor_id WHERE l.deleted_at=0 AND l.pipeline_id IN (""" + ",".join("?" for _ in ids) + """ )
            AND (ct.name LIKE ? ESCAPE '\\' OR ct.phone LIKE ? ESCAPE '\\') ORDER BY l.updated_at DESC LIMIT 30""", (*ids, pattern, pattern))]


def save(store, auth, user, payload, action="save"):
    now = time.time()
    actor, uid = user["nome"], str(user["id"])
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        old = None
        if payload.get("id"):
            old = c.execute("SELECT * FROM crm_reminders WHERE id=?", (text(payload["id"], 80),)).fetchone()
            if not old:
                raise LookupError("Lembrete nao encontrado.")
            lead = store.row(c, "leads", old["lead_id"])
            if integer(payload.get("revision"), 1, 1000000000) != old["revision"]:
                raise Conflict("Esta tarefa foi atualizada. Atualize a lista antes de continuar.")
        else:
            if action != "save":
                raise ValueError("Selecione uma tarefa.")
            lead = store.row(c, "leads", text(payload.get("lead_id"), 80))
        key = old["id"] if old else str(uuid.uuid4())
        if action == "ack":
            if old["assignee_id"] != uid:
                raise PermissionError("Apenas o responsavel pode dispensar o proprio aviso.")
            c.execute("UPDATE crm_reminders SET acknowledged_at=?,revision=revision+1 WHERE id=?", (now, key))
        elif action in {"done", "cancel", "reopen"}:
            status = {"done": "done", "cancel": "cancelled", "reopen": "pending"}[action]
            if old["status"] != status:
                c.execute("UPDATE crm_reminders SET status=?,completed_at=?,acknowledged_at=0,updated_at=?,revision=revision+1 WHERE id=?",
                          (status, now if status == "done" else 0, now, key))
                store.event(c, lead["id"], actor, "reminder", {"done": "Tarefa concluida: ", "cancel": "Tarefa cancelada: ", "reopen": "Tarefa reaberta: "}[action] + old["title"], now)
        elif action == "save":
            title, note, due = text(payload.get("title"), 160), text(payload.get("note", ""), 2000, required=False), local_due(payload.get("due_local"))
            assigned = assignee_allowed(store, auth, text(str(payload.get("assignee_id", uid)), 80), lead)
            if not assigned:
                raise ValueError("O responsavel precisa estar ativo e ter acesso a este cliente e funil.")
            assigned_id = str(assigned["id"])
            if old:
                if old["status"] != "pending":
                    raise Conflict("Reabra a tarefa antes de editar.")
                reset = due != old["due_at"] or assigned_id != old["assignee_id"]
                c.execute("""UPDATE crm_reminders SET title=?,note=?,due_at=?,assignee_id=?,assignee_name=?,
                    acknowledged_at=CASE WHEN ? THEN 0 ELSE acknowledged_at END,updated_at=?,revision=revision+1 WHERE id=?""",
                    (title, note, due, assigned_id, assigned["nome"], reset, now, key))
            else:
                request = str(uuid.UUID(text(payload.get("request_id"), 80)))
                existing = c.execute("SELECT * FROM crm_reminders WHERE created_by=? AND request_id=?", (uid, request)).fetchone()
                if existing:
                    if (existing["lead_id"], existing["title"], existing["note"], existing["due_at"], existing["assignee_id"]) != (lead["id"], title, note, due, assigned_id):
                        raise Conflict("Este pedido ja foi usado para outra tarefa.")
                    return serialize(c.execute(SELECT + " WHERE r.id=?", (existing["id"],)).fetchone(), now)
                c.execute("""INSERT INTO crm_reminders(id,lead_id,title,note,due_at,assignee_id,assignee_name,created_by,request_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (key, lead["id"], title, note, due, assigned_id, assigned["nome"], uid, request, now, now))
            schedule = datetime.fromtimestamp(due, TZ).strftime("%d/%m/%Y as %H:%M")
            store.event(c, lead["id"], actor, "reminder", ("Lembrete atualizado: " if old else "Lembrete criado: ") + title + " · " + schedule + " · " + assigned["nome"], now)
        else:
            raise ValueError("Acao de tarefa invalida.")
        return serialize(c.execute(SELECT + " WHERE r.id=?", (key,)).fetchone(), now)


def handle(handler, store, action, query, user):
    auth = handler.server.auth_store
    if handler.command == "GET":
        if action == "reminders":
            return handler.auth_json({"ok": True, **listing(store, user, query)})
        if action == "reminders/alerts":
            return handler.auth_json({"ok": True, **listing(store, user, {}, alerts=True)})
        if action == "reminders/leads":
            return handler.auth_json({"ok": True, "leads": lead_choices(store, query)})
        if action == "reminders/options":
            with store.db() as c:
                lead = store.row(c, "leads", query.get("lead", [""])[0])
            return handler.auth_json({"ok": True, "assignees": assignees(store, auth, lead)})
    elif handler.command == "POST" and action in {"reminders/save", "reminders/done", "reminders/cancel", "reminders/reopen", "reminders/ack"}:
        if not handler.same_origin_request():
            return handler.auth_json({"ok": False, "error": "Origem nao autorizada."}, 403)
        payload = json.loads(read_body(handler, 16 * 1024))
        if not isinstance(payload, dict):
            raise ValueError("Dados invalidos.")
        item = save(store, auth, user, payload, action.rsplit("/", 1)[1])
        auth.audit_event("crm_reminder_" + action.rsplit("/", 1)[1], user["id"], details={"clinic": "vielle", "id": item["id"]})
        return handler.auth_json({"ok": True, "item": item})
    return handler.auth_json({"ok": False, "error": "Rota de tarefas nao encontrada."}, 404)
