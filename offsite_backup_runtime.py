"""Server-side scheduling and sanitized health; never decrypts or starts CRM workers."""
from datetime import datetime, timedelta
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
from zoneinfo import ZoneInfo

from offsite_backup import BackupError, regular_open
from offsite_backup_service import atomic_json, read_state, run_once, secure_directory

ZONE = ZoneInfo("America/Sao_Paulo")


def environment(data_dir, environ=None):
    result = dict(os.environ if environ is None else environ)
    result["DATA_DIR"] = str(Path(data_dir).resolve())
    return result


def health(workspace):
    path = workspace / "health.json"
    if not path.exists():
        return {"version": 1, "status": "waiting", "attempted_at": 0, "retry_at": 0, "failures": 0, "error": ""}
    with regular_open(path)[0] as handle:
        raw = handle.read(16385)
    if len(raw) > 16384:
        raise BackupError("invalid_backup_health")
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get("version") != 1
            or value.get("status") not in {"waiting", "running", "uploaded", "failed", "review"}
            or type(value.get("failures")) is not int or not 0 <= value["failures"] <= 6
            or any(type(value.get(k)) not in {int, float} or not math.isfinite(value[k]) or value[k] < 0
                   for k in ("attempted_at", "retry_at"))
            or not isinstance(value.get("error"), str)):
        raise BackupError("invalid_backup_health")
    return value


def hour(env):
    value = env.get("DOC4DOCS_OFFSITE_BACKUP_HOUR", "3")
    if not isinstance(value, str) or not value.isdigit() or not 0 <= int(value) <= 23:
        raise BackupError("invalid_backup_schedule")
    return int(value)


def next_slot(now, target):
    local = datetime.fromtimestamp(now, ZONE)
    slot = local.replace(hour=target, minute=0, second=0, microsecond=0)
    return (slot if slot.timestamp() > now else slot + timedelta(days=1)).timestamp()


def overview(data_dir, environ=None):
    env = environment(data_dir, environ)
    workspace = Path(env["DATA_DIR"]) / ".offsite-backup"
    enabled = env.get("DOC4DOCS_OFFSITE_BACKUP_ENABLED") == "1"
    state = read_state(workspace / "state.json")
    current = health(workspace)
    now = time.time()
    error = current.get("error", "")
    if not re.fullmatch(r"[a-z0-9_]{0,80}", error):
        error = "backup_failed"
    folder = state.get("folder_id", "")
    folder = folder if re.fullmatch(r"[A-Za-z0-9_-]{10,200}", folder) else ""
    last = state["last_uploaded"]
    return {"enabled": enabled, "status": current["status"] if enabled else "disabled",
            "last_uploaded": last, "last_attempt": current["attempted_at"], "error": error,
            "needs_attention": bool(enabled and (current["status"] in {"failed", "review"} or last and now - last > 48 * 3600)),
            "next_attempt": (current["retry_at"] or next_slot(now, hour(env)))
                            if enabled and current["status"] != "review" else 0,
            "schedule_hour": hour(env), "timezone": str(ZONE),
            "folder_url": "https://drive.google.com/drive/folders/" + folder if folder else "",
            "retention": "all", "restore_verified": False}


def request_retry(data_dir, environ=None):
    env = environment(data_dir, environ)
    if env.get("DOC4DOCS_OFFSITE_BACKUP_ENABLED") != "1":
        raise BackupError("backup_disabled")
    workspace = secure_directory(Path(env["DATA_DIR"]) / ".offsite-backup")
    if health(workspace)["status"] == "running":
        return {"status": "running"}
    atomic_json(workspace / "request.json", {"requested_at": time.time()})
    return {"status": "requested"}


def cycle(data_dir, environ=None, *, runner=run_once, now=None):
    env = environment(data_dir, environ)
    if env.get("DOC4DOCS_OFFSITE_BACKUP_ENABLED") != "1":
        return {"status": "disabled"}
    now = time.time() if now is None else now
    target = hour(env)
    workspace = secure_directory(Path(env["DATA_DIR"]) / ".offsite-backup")
    state, current = read_state(workspace / "state.json"), health(workspace)
    requested = (workspace / "request.json").exists()
    if current["status"] == "review" and not requested:
        return {"status": "review"}
    if current["retry_at"] > now and not requested:
        return {"status": "waiting"}
    if not requested and state["last_uploaded"] and not state["active"] and current["status"] != "failed":
        if now < next_slot(state["last_uploaded"], target):
            return {"status": "waiting"}
    if requested:
        (workspace / "request.json").unlink()
        current["failures"] = 0
        if state["active"]:
            # Explicit review retries the same encrypted file and resumable upload.
            state["active"]["attempts"] = 0
            atomic_json(workspace / "state.json", state)
    current.update(status="running", attempted_at=now, retry_at=0, error="")
    atomic_json(workspace / "health.json", current)
    try:
        result = runner(env)
    except Exception as exc:
        failures = min(6, current["failures"] + 1)
        code = str(exc) if isinstance(exc, BackupError) else "backup_failed"
        code = code if re.fullmatch(r"[a-z0-9_]{1,80}", code) else "backup_failed"
        current.update(status="review" if failures == 6 else "failed", failures=failures,
                       retry_at=0 if failures == 6 else now + min(3600, 600 * 2 ** (failures - 1)), error=code)
        atomic_json(workspace / "health.json", current)
        logging.warning("DOC4DOCS external backup requires attention: %s", code)
        return {"status": current["status"], "error": code}
    current.update(status="uploaded" if result["status"] in {"uploaded_checksum_verified", "not_due"} else "waiting",
                   failures=0, retry_at=next_slot(now, target), error="")
    atomic_json(workspace / "health.json", current)
    return result


def run(data_dir, stop, environ=None):
    while not stop.is_set():
        try:
            cycle(data_dir, environ)
        except Exception:
            logging.warning("DOC4DOCS external backup scheduler unavailable; review Master status")
        stop.wait(60)


def start(data_dir):
    if os.environ.get("DOC4DOCS_OFFSITE_BACKUP_ENABLED") != "1":
        return None
    worker = threading.Thread(target=run, args=(data_dir, threading.Event()), daemon=True, name="offsite-backup")
    worker.start()
    return worker
