"""Clinic-local tasks and immutable activity history, separate from integration data."""
import contextlib
import json
from pathlib import Path
import sqlite3
from datetime import date, datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

STATUSES = {"todo": "Não iniciada", "doing": "Em andamento", "done": "Concluída"}
PRIORITIES = {"low": "Baixa", "normal": "Normal", "high": "Alta"}


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def today():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def text(value, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"Texto inválido (limite: {limit} caracteres).")
    return value.strip()


def task_date(value):
    if value == "":
        return ""
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Data inválida.")
    return value


class TaskStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.files = self.root / "files"
        self.files.mkdir(exist_ok=True)
        self.path = self.root / "tasks.sqlite3"
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    status TEXT NOT NULL, priority TEXT NOT NULL, start_date TEXT NOT NULL,
                    due_date TEXT NOT NULL, assignees TEXT NOT NULL, checklist TEXT NOT NULL,
                    progress INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    creator_id INTEGER NOT NULL, creator_name TEXT NOT NULL,
                    completed_at TEXT, archived_at TEXT, revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS tasks_dates ON tasks(archived_at, due_date, created_at);
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id),
                    actor_id INTEGER NOT NULL, actor_name TEXT NOT NULL, created_at TEXT NOT NULL,
                    action TEXT NOT NULL, details TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_events_task ON task_events(task_id, id);
                CREATE TABLE IF NOT EXISTS task_files (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), name TEXT NOT NULL,
                    size INTEGER NOT NULL, created_at TEXT NOT NULL, actor_name TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_files_task ON task_files(task_id);
            """)

    @contextlib.contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _row(self, conn, task_id):
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise LookupError("Tarefa não encontrada.")
        return dict(row)

    def _serialize(self, row):
        row = dict(row)
        row["assignees"] = json.loads(row["assignees"])
        row["checklist"] = json.loads(row["checklist"])
        return row

    def _event(self, conn, task_id, actor, action, details):
        conn.execute("INSERT INTO task_events(task_id,actor_id,actor_name,created_at,action,details) VALUES(?,?,?,?,?,?)",
                     (task_id, actor["id"], actor["nome"], now(), action, json.dumps(details, ensure_ascii=False)))

    def _values(self, payload, members, old=None):
        allowed = {"title", "description", "status", "priority", "start_date", "due_date", "assignees", "checklist", "progress", "revision"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("Campos da tarefa inválidos.")
        values = {"title": "", "description": "", "status": "todo", "priority": "normal",
                  "start_date": "", "due_date": "", "assignees": [], "checklist": [], "progress": 0}
        if old:
            values.update({key: old[key] for key in values})
        values.update({key: value for key, value in payload.items() if key != "revision"})
        values["title"] = text(values["title"], 200, True)
        values["description"] = text(values["description"], 10000)
        if values["status"] not in STATUSES or values["priority"] not in PRIORITIES:
            raise ValueError("Status ou prioridade inválido.")
        for key in ("start_date", "due_date"):
            values[key] = task_date(values[key])
        if values["start_date"] and values["due_date"] and values["start_date"] > values["due_date"]:
            raise ValueError("O vencimento não pode ser anterior ao início.")
        if "assignees" in payload or not old:
            ids = values["assignees"]
            if not isinstance(ids, list) or len(ids) > 50 or any(type(i) is not int for i in ids):
                raise ValueError("Responsáveis inválidos.")
            people = {u["id"]: u for u in members}
            # Preserve old assignments when someone was deactivated; do not assign new unauthorized users.
            previous = {u["id"]: u for u in old["assignees"]} if old else {}
            if any(i not in people and i not in previous for i in ids):
                raise ValueError("Escolha responsáveis com acesso a Tarefas nesta clínica.")
            values["assignees"] = [people.get(i, previous.get(i)) for i in dict.fromkeys(ids)]
        items = values["checklist"]
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("Use no máximo 100 itens no checklist.")
        clean, seen = [], set()
        for item in items:
            if not isinstance(item, dict) or type(item.get("done", False)) is not bool:
                raise ValueError("Item de checklist inválido.")
            key = item.get("id") or uuid4().hex
            if not isinstance(key, str) or len(key) > 64 or key in seen:
                raise ValueError("Identificador de checklist inválido.")
            seen.add(key)
            clean.append({"id": key, "text": text(item.get("text"), 300, True), "done": item.get("done", False)})
        values["checklist"] = clean
        if type(values["progress"]) is not int or not 0 <= values["progress"] <= 100:
            raise ValueError("O progresso deve estar entre 0 e 100.")
        if clean:
            values["progress"] = round(sum(i["done"] for i in clean) / len(clean) * 100)
        if values["status"] == "done":
            values["progress"] = 100
        return values

    def create(self, payload, actor, members):
        values = self._values(payload, members)
        key, stamp = uuid4().hex, now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT INTO tasks(id,title,description,status,priority,start_date,due_date,
                assignees,checklist,progress,created_at,updated_at,creator_id,creator_name,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (key, values["title"], values["description"], values["status"],
                values["priority"], values["start_date"], values["due_date"], json.dumps(values["assignees"]),
                json.dumps(values["checklist"]), values["progress"], stamp, stamp, actor["id"], actor["nome"],
                stamp if values["status"] == "done" else None))
            self._event(conn, key, actor, "created", values)
        return self.detail(key)

    def update(self, key, payload, actor, members):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = self._serialize(self._row(conn, key))
            if old["archived_at"]:
                raise ValueError("Restaure a tarefa antes de editar.")
            if type(payload.get("revision")) is not int or payload["revision"] != old["revision"]:
                raise Conflict("Esta tarefa foi alterada por outra pessoa. Reabra os detalhes antes de salvar.")
            values = self._values(payload, members, old)
            changes = {k: {"before": old[k], "after": v} for k, v in values.items() if old[k] != v}
            if changes:
                stamp = now()
                completed = (old["completed_at"] or stamp) if values["status"] == "done" else None
                conn.execute("""UPDATE tasks SET title=?,description=?,status=?,priority=?,start_date=?,due_date=?,
                    assignees=?,checklist=?,progress=?,updated_at=?,completed_at=?,revision=revision+1 WHERE id=?""",
                    (values["title"], values["description"], values["status"], values["priority"], values["start_date"],
                     values["due_date"], json.dumps(values["assignees"]), json.dumps(values["checklist"]),
                     values["progress"], stamp, completed, key))
                self._event(conn, key, actor, "updated", changes)
        return self.detail(key)

    def archive(self, key, archived, revision, actor):
        if type(archived) is not bool:
            raise ValueError("Estado de arquivamento inválido.")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, key)
            if type(revision) is not int or row["revision"] != revision:
                raise Conflict("A tarefa mudou. Reabra antes de arquivar ou restaurar.")
            stamp = now()
            conn.execute("UPDATE tasks SET archived_at=?,updated_at=?,revision=revision+1 WHERE id=?", (stamp if archived else None, stamp, key))
            self._event(conn, key, actor, "archived" if archived else "restored", {})
        return self.detail(key)

    def comment(self, key, message, actor):
        message = text(message, 10000, True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._row(conn, key)["archived_at"]:
                raise ValueError("Restaure a tarefa antes de comentar.")
            self._event(conn, key, actor, "comment", {"text": message})
            conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), key))
        return self.detail(key)

    def attach(self, key, name, content, actor):
        file_id, stamp = uuid4().hex, now()
        path = self.files / file_id
        try:
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if self._row(conn, key)["archived_at"]:
                    raise ValueError("Restaure a tarefa antes de anexar.")
                count, size = conn.execute("SELECT count(*),coalesce(sum(size),0) FROM task_files WHERE task_id=?", (key,)).fetchone()
                if count >= 50 or size + len(content) > 200 * 1024 * 1024:
                    raise ValueError("Limite por tarefa: 50 arquivos ou 200 MB.")
                path.write_bytes(content)
                conn.execute("INSERT INTO task_files VALUES(?,?,?,?,?,?)", (file_id, key, name, len(content), stamp, actor["nome"]))
                self._event(conn, key, actor, "attachment", {"name": name, "file_id": file_id, "size": len(content)})
                conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (stamp, key))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.detail(key)

    def events(self, key, before=None):
        with self.connection() as conn:
            self._row(conn, key)
            rows = conn.execute("SELECT * FROM task_events WHERE task_id=? AND id<? ORDER BY id DESC LIMIT 101", (key, before or 9223372036854775807)).fetchall()
        return {"events": [{**dict(r), "details": json.loads(r["details"])} for r in rows[:100]],
                "before": rows[99]["id"] if len(rows) > 100 else None}

    def detail(self, key):
        with self.connection() as conn:
            row = self._serialize(self._row(conn, key))
            row["attachments"] = [dict(r) for r in conn.execute("SELECT * FROM task_files WHERE task_id=? ORDER BY created_at", (key,))]
        row.update(self.events(key))
        return row

    def file(self, key):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM task_files WHERE id=?", (key,)).fetchone()
        if not row:
            raise LookupError("Arquivo não encontrado.")
        return dict(row), self.files / row["id"]

    def listing(self, query):
        clauses, params = ["archived_at IS NOT NULL" if query.get("archived") == "1" else "archived_at IS NULL"], []
        if query.get("q"):
            clauses.append("(instr(lower(title),lower(?))>0 OR instr(lower(description),lower(?))>0)")
            params += [query["q"][:200]] * 2
        if query.get("assignee"):
            clauses.append("EXISTS(SELECT 1 FROM json_each(tasks.assignees) WHERE json_extract(value,'$.id')=?)")
            params.append(int(query["assignee"]))
        if query.get("due") in {"today", "overdue", "none"}:
            clauses.append({"today": "due_date=? AND status!='done'", "overdue": "due_date!='' AND due_date<? AND status!='done'", "none": "due_date=''"}[query["due"]])
            if query["due"] != "none": params.append(today())
        offset = max(0, min(1000000, int(query.get("offset", "0"))))
        where = " AND ".join(clauses)
        with self.connection() as conn:
            rows = conn.execute(f"""SELECT tasks.*,
                (SELECT count(*) FROM task_files WHERE task_id=tasks.id) AS attachment_count,
                (SELECT count(*) FROM task_events WHERE task_id=tasks.id AND action='comment') AS comment_count
                FROM tasks WHERE {where} ORDER BY created_at DESC,id LIMIT 101 OFFSET ?""", [*params, offset]).fetchall()
            total = conn.execute(f"SELECT count(*) FROM tasks WHERE {where}", params).fetchone()[0]
        return {"tasks": [self._serialize(row) for row in rows[:100]], "total": total,
                "next_offset": offset + 100 if len(rows) > 100 else None, "today": today()}
