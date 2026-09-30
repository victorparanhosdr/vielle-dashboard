"""Authenticated task routes. Files never live under the public static directory."""
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import parse_qs, quote

from task_store import Conflict, TaskStore

MAX_FILE = 10 * 1024 * 1024
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xlsx", ".xls", ".csv", ".doc", ".docx", ".txt", ".pptx", ".zip"}
STORES, LOCK = {}, threading.Lock()


def members(auth, clinic):
    with auth.connection() as conn:
        rows = conn.execute("""SELECT id,nome FROM users u WHERE status='active' AND
            (is_master=1 OR (EXISTS(SELECT 1 FROM user_clinics WHERE user_id=u.id AND clinic_key=?)
             AND EXISTS(SELECT 1 FROM user_clinic_permissions WHERE user_id=u.id AND clinic_key=? AND permission_key='tasks.view')))
            ORDER BY nome COLLATE NOCASE,id""", (clinic, clinic)).fetchall()
    return [{"id": r["id"], "name": r["nome"]} for r in rows]


def store_for(auth, clinic):
    root = auth.path.parent / "tasks" / clinic
    with LOCK:
        if root not in STORES:
            STORES[root] = TaskStore(root)
        return STORES[root]


def read_body(handler, maximum):
    length = int(handler.headers.get("Content-Length", "0"))
    if not 0 < length <= maximum or handler.headers.get("Transfer-Encoding"):
        raise ValueError(f"Envie um arquivo de até {maximum // 1024 // 1024} MB." if maximum == MAX_FILE else "Solicitação inválida ou muito grande.")
    handler.connection.settimeout(30)
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ValueError("Envio incompleto. Tente novamente.")
    return raw


def handle_request(handler, parsed):
    query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    clinic = query["clinic"]
    parts = parsed.path.strip("/").split("/")
    key = parts[2] if len(parts) >= 3 else ""
    action = parts[3] if len(parts) == 4 else ""
    actor = handler.current_user
    try:
        store = store_for(handler.server.auth_store, clinic)
        if len(parts) > 4 or (key and not re.fullmatch(r"[a-f0-9]{32}", key)):
            raise LookupError("Rota não encontrada.")
        if handler.command == "GET":
            if not key:
                return handler.auth_json({"ok": True, **store.listing(query), "users": members(handler.server.auth_store, clinic)})
            if action == "file":
                meta, path = store.file(key)
                content = path.read_bytes()
                handler.send_response(200)
                handler.send_header("Content-Type", "application/octet-stream")
                handler.send_header("Content-Length", str(len(content)))
                handler.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(meta["name"], safe=""))
                handler.send_header("Content-Security-Policy", "sandbox")
                handler.end_headers()
                handler.wfile.write(content)
                return
            if action == "events":
                return handler.auth_json({"ok": True, **store.events(key, int(query.get("before", "0")))})
            if not action:
                return handler.auth_json({"ok": True, "task": store.detail(key)})
            raise LookupError("Rota não encontrada.")
        if handler.command != "POST":
            return handler.auth_json({"ok": False, "error": "Método não permitido."}, 405)
        if action == "attachments":
            name = query.get("name", "")
            if not name or len(name) > 180 or any(ord(c) < 32 for c in name) or "/" in name or "\\" in name or Path(name).suffix.lower() not in EXTENSIONS:
                raise ValueError("Nome ou formato de arquivo não permitido.")
            result = store.attach(key, name, read_body(handler, MAX_FILE), actor)
        else:
            payload = json.loads(read_body(handler, 128 * 1024))
            if not isinstance(payload, dict):
                raise ValueError("Solicitação inválida.")
            if not key:
                result = store.create(payload, actor, members(handler.server.auth_store, clinic))
            elif action == "comments":
                result = store.comment(key, payload.get("text"), actor)
            elif action == "archive":
                result = store.archive(key, payload.get("archived"), payload.get("revision"), actor)
            elif not action:
                result = store.update(key, payload, actor, members(handler.server.auth_store, clinic))
            else:
                raise LookupError("Rota não encontrada.")
        return handler.auth_json({"ok": True, "task": result}, 201 if not key else 200)
    except Conflict as error:
        return handler.auth_json({"ok": False, "error": str(error)}, 409)
    except (ValueError, TypeError, OverflowError) as error:
        return handler.auth_json({"ok": False, "error": str(error) if isinstance(error, ValueError) else "Dados inválidos."}, 400)
    except (LookupError, FileNotFoundError):
        return handler.auth_json({"ok": False, "error": "Tarefa ou arquivo não encontrado."}, 404)
    except sqlite3.OperationalError:
        return handler.auth_json({"ok": False, "error": "Banco ocupado. Tente novamente em instantes."}, 503)
    except Exception:
        logging.exception("Task operation failed for clinic %s", clinic)
        return handler.auth_json({"ok": False, "error": "Não foi possível concluir a operação."}, 500)
