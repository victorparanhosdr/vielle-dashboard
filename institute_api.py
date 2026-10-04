"""Institute routes reuse login, never clinic authorization or clinical records."""
import csv
import json
import logging
import posixpath
import sqlite3
import threading
import time
from urllib.parse import parse_qs, unquote

from institute_report import build_report, workbook
from institute_sources import Kiwify, Meta, SourceError, kommo, period, sheet_rows, sheets
from institute_store import InstituteStore

STORES, JOBS, LOCK = {}, {}, threading.Lock()


def store_for(auth):
    path = auth.path.parent / "institutes" / "institutes.sqlite3"
    with LOCK:
        if path not in STORES:
            store = InstituteStore(path)
            store.initialize()
            STORES[path] = store
        return STORES[path]


def is_institute_request(parsed):
    path = posixpath.normpath(unquote(parsed.path)).removeprefix("/static")
    return path in {"/institutos", "/institutes.html", "/api/institutes"} or path.startswith("/api/institutes/")


def guard(handler, parsed):
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "clinic" in query or "view" in query or any(len(v) != 1 for v in query.values()):
        handler.auth_json({"ok": False, "error": "Informe apenas o instituto e o curso, sem parâmetros de clínica."}, 400)
        return False
    path = posixpath.normpath(unquote(parsed.path)).removeprefix("/static")
    if handler.command not in {"GET", "POST", "HEAD"}:
        handler.auth_json({"ok": False, "error": "Método não permitido."}, 405)
        return False
    store = store_for(handler.server.auth_store)
    if path == "/api/institutes":
        return True
    if path in {"/institutos", "/institutes.html"} and "institute" not in query:
        return True
    institute = query.get("institute", ["victor-paranhos"])[0]
    if institute not in {row["key"] for row in store.institutes()}:
        handler.auth_json({"ok": False, "error": "Instituto não encontrado."}, 404)
        return False
    if institute not in store.allowed(handler.current_user):
        handler.auth_json({"ok": False, "code": "institute_forbidden", "error": "Seu usuário não possui acesso a este instituto."}, 403)
        return False
    if path.split("/")[-1] in {"settings", "products", "meta-campaigns", "courses", "campaigns", "members", "import-leads"}:
        return handler.require_master_auth()
    return True


def body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if not 0 < length <= 8 * 1024 * 1024 or handler.headers.get_content_type() != "application/json" or handler.headers.get("Transfer-Encoding"):
        raise ValueError("Envie JSON de até 8 MB.")
    handler.connection.settimeout(30)
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ValueError("Envio incompleto.")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Solicitação inválida.")
    return payload


def sync_sources(store, institute, course, start, end, kommo_call, progress):
    info = store.course(institute, course)
    config = store.settings(institute)
    targets = {"kiwify": lambda: Kiwify(config).sales(info, min(start, "2025-01-01"), end)}
    if info["sheet_id"]:
        targets["sheets"] = lambda: sheets(info)
    if info["pipeline_name"]:
        targets["kommo"] = lambda: kommo(info, kommo_call)
    if config.get("meta_access_token") and store.campaigns(institute, course):
        targets["meta"] = lambda: Meta(config).insights(store.campaigns(institute, course), start, end)
    results = {}
    for source, fetch in targets.items():
        progress(source)
        previous = store.sync_state(institute, course).get(source, {})
        try:
            rows = fetch()
            window = ("created_day", min(start, "2025-01-01"), end) if source == "kiwify" else ("day", start, end) if source == "meta" else None
            store.save_records(institute, course, source, rows, snapshot=source in {"sheets", "kommo"}, window=window)
            state = {"ok": True, "count": len(rows), "at": int(time.time()),
                     "from": min(start, "2025-01-01") if source == "kiwify" else start, "to": end}
        except Exception as exc:
            message = str(exc) if isinstance(exc, SourceError) else "Não foi possível atualizar esta fonte. Os dados anteriores foram preservados."
            state = {**previous, "ok": False, "error": message, "attempt_at": int(time.time())}
            logging.warning("Institute sync failed for source %s", source)
        store.set_sync_state(institute, course, source, state)
        results[source] = state
    return results


def job_key(store, institute, course):
    return (str(store.path), institute, course)


def handle_request(handler, parsed, kommo_call):
    store = store_for(handler.server.auth_store)
    query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    institute, course = query.get("institute", "victor-paranhos"), query.get("course", "regen-code")
    path = parsed.path
    actor = handler.current_user
    try:
        if path == "/api/institutes" and handler.command == "GET":
            allowed = set(store.allowed(actor))
            return handler.auth_json({"ok": True, "institutes": [{**row, "courses": store.courses(row["key"])}
                for row in store.institutes() if row["key"] in allowed]})
        info = store.course(institute, course)
        if not info:
            raise LookupError()
        if handler.command == "GET":
            if path == "/api/institutes/report":
                return handler.auth_json(build_report(store, institute, course, query))
            if path == "/api/institutes/export":
                data = workbook(store, institute, course, query)
                handler.send_response(200)
                handler.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                handler.send_header("Content-Disposition", f'attachment; filename="doc4docs-{course}.xlsx"')
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)
                return
            if path == "/api/institutes/sync":
                with LOCK:
                    state = dict(JOBS.get(job_key(store, institute, course), {"running": False}))
                return handler.auth_json({"ok": True, **state})
            if path == "/api/institutes/settings":
                with handler.server.auth_store.connection() as conn:
                    users = [dict(r) for r in conn.execute("SELECT id,nome,is_master FROM users WHERE status='active' ORDER BY nome")]
                utms = sorted({r.get("campaign") for source in ("sheets", "kiwify") for r in store.records(institute, course, source) if r.get("campaign")})
                return handler.auth_json({"ok": True, "settings": store.public_settings(institute), "courses": store.courses(institute),
                    "campaigns": store.campaigns(institute, course), "users": users, "members": store.members(institute), "utms": utms})
            if path == "/api/institutes/products":
                return handler.auth_json({"ok": True, "products": Kiwify(store.settings(institute)).products()})
            if path == "/api/institutes/meta-campaigns":
                return handler.auth_json({"ok": True, **Meta(store.settings(institute)).campaigns()})
        elif handler.command == "POST":
            payload = body(handler)
            if path == "/api/institutes/sync":
                if set(payload) != {"from", "to"}:
                    raise ValueError("Período inválido.")
                period(payload["from"], payload["to"])
                ident = job_key(store, institute, course)
                with LOCK:
                    last = JOBS.get(ident, {})
                    if last.get("running"):
                        return handler.auth_json({"ok": True, **last}, 202)
                    if time.monotonic() - last.get("started_monotonic", 0) < 30:
                        return handler.auth_json({"ok": False, "error": "Aguarde 30 segundos entre atualizações."}, 429)
                    JOBS[ident] = {"running": True, "started_monotonic": time.monotonic(), "source": "kiwify"}
                def progress(source):
                    with LOCK:
                        JOBS[ident]["source"] = source
                def run():
                    try:
                        result = sync_sources(store, institute, course, payload["from"], payload["to"], kommo_call, progress)
                        handler.server.auth_store.audit_event("institute_sync", actor["id"], details={"institute": institute, "course": course, "sources": {k: v["ok"] for k,v in result.items()}})
                    finally:
                        with LOCK:
                            JOBS[ident].update(running=False, source="", finished_at=int(time.time()))
                threading.Thread(target=run, daemon=True).start()
                return handler.auth_json({"ok": True, "running": True}, 202)
            with LOCK:
                if any(ident[:2] == (str(store.path), institute) and job.get("running")
                       for ident, job in JOBS.items()):
                    return handler.auth_json({"ok": False, "error": "Aguarde a atualização terminar antes de alterar a configuração."}, 409)
            if path == "/api/institutes/settings":
                store.save_settings(institute, payload)
            elif path == "/api/institutes/courses":
                store.save_course(institute, payload)
            elif path == "/api/institutes/campaigns" and set(payload) == {"campaigns"}:
                store.save_campaigns(institute, course, payload["campaigns"])
                store.set_sync_state(institute, course, "meta", {"ok": False, "error": "Campanhas alteradas. Atualize o investimento para este período."})
            elif path == "/api/institutes/members" and set(payload) == {"user_ids"}:
                ids = payload["user_ids"]
                if not isinstance(ids, list):
                    raise ValueError("Usuários inválidos.")
                with handler.server.auth_store.connection() as conn:
                    valid = {r[0] for r in conn.execute("SELECT id FROM users WHERE status='active' AND is_master=0")}
                if any(type(v) is not int or v not in valid for v in ids):
                    raise ValueError("Selecione usuários comuns e ativos.")
                store.save_members(institute, ids)
            elif path == "/api/institutes/import-leads" and set(payload) == {"csv"} and isinstance(payload["csv"], str):
                rows = sheet_rows(payload["csv"])
                store.save_records(institute, course, "sheets", rows, snapshot=True)
                store.set_sync_state(institute, course, "sheets", {"ok": True, "count": len(rows), "at": int(time.time()), "method": "csv"})
            else:
                raise LookupError()
            handler.server.auth_store.audit_event("institute_configured", actor["id"], details={"institute": institute, "course": course, "operation": path.split("/")[-1]})
            return handler.auth_json({"ok": True})
        raise LookupError()
    except LookupError:
        return handler.auth_json({"ok": False, "error": "Rota ou curso não encontrado."}, 404)
    except SourceError as exc:
        return handler.auth_json({"ok": False, "error": str(exc)}, 502)
    except (ValueError, TypeError, OverflowError, csv.Error) as exc:
        return handler.auth_json({"ok": False, "error": str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "Dados inválidos."}, 400)
    except sqlite3.Error:
        return handler.auth_json({"ok": False, "error": "Banco temporariamente indisponível. Tente novamente."}, 503)
    except Exception:
        logging.error("Institute request failed: %s", path)
        return handler.auth_json({"ok": False, "error": "Não foi possível concluir a solicitação. Tente novamente."}, 500)
