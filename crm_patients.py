"""Read-only phone index of the Vielle Clinic Experts patient cache."""
import json
from pathlib import Path
import re
import sqlite3
import threading
import time


def experts_phone(value):
    if not isinstance(value, (str, int)):
        return ""
    raw = str(value).strip()
    number = re.sub(r"\D", "", raw)
    if not 10 <= len(number) <= 15:
        return ""
    if not raw.startswith("+") and len(number) in {10, 11}:
        number = "55" + number
    return number


class PatientDirectory:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.checked = 0
        self.available = False
        self.index = {}
        self.synced_at = 0

    def refresh(self):
        if time.monotonic() - self.checked < 15:
            return
        self.checked = time.monotonic()
        if not self.path.exists():
            self.available = False
            return
        try:
            c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=3)
            try:
                rows = c.execute("SELECT uuid,name,phone,raw_json,synced_at FROM clinica_patients").fetchall()
            finally:
                c.close()
            index = {}
            synced = 0
            for uid, name, phone, raw_json, updated in rows:
                try:
                    raw = json.loads(raw_json or "{}")
                except (ValueError, TypeError):
                    raw = {}
                if not isinstance(raw, dict):
                    raw = {}
                if raw.get("deleted") is True or raw.get("deleted_at") or str(raw.get("status", "")).lower() in {"deleted", "removed", "excluido"}:
                    continue
                values = [phone, *(raw.get(k) for k in ("phone", "cellphone", "mobile", "telephone", "whatsapp"))]
                for key in ("phones", "telephones"):
                    extras = raw.get(key)
                    if isinstance(extras, list):
                        values.extend(v.get("number") or v.get("phone") or v.get("value") if isinstance(v, dict) else v for v in extras)
                for value in values:
                    number = experts_phone(value)
                    if number:
                        index.setdefault(number, {})[str(uid)] = {"uuid": str(uid), "name": str(name or raw.get("name") or "").strip()[:160]}
                synced = max(synced, int(updated or 0))
            self.index, self.available, self.synced_at = index, bool(rows), synced
        except (sqlite3.Error, OSError, TypeError, ValueError):
            self.available = False

    def lookup(self, number):
        # Never match by the last 8/9 digits: identical numbers in other DDDs
        # are different people. Multiple patients sharing a phone need review.
        with self.lock:
            self.refresh()
            if not self.available:
                return {"status": "unchecked", "synced_at": self.synced_at}
            candidates = list(self.index.get(re.sub(r"\D", "", number), {}).values())
            if len(candidates) == 1 and candidates[0]["name"]:
                return {"status": "matched", **candidates[0], "synced_at": self.synced_at}
            return {"status": "review" if candidates else "new", "candidates": candidates, "synced_at": self.synced_at}
