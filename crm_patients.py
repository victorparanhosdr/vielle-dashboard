"""Read-only phone index of the Vielle Clinic Experts patient cache."""
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
import phonenumbers


def phone_keys(value):
    """Full numbers only; pair a valid Brazilian mobile with its legacy form."""
    number = re.sub(r"\D", "", str(value)) if isinstance(value, (str, int)) else ""
    if not 10 <= len(number) <= 15:
        return ()
    modern = number[:4] + "9" + number[4:] if len(number) == 12 else number
    if number.startswith("55") and re.fullmatch(r"55\d{2}9[6-9]\d{7}", modern):
        parsed = phonenumbers.parse("+" + modern, None)
        if phonenumbers.is_valid_number(parsed) and phonenumbers.number_type(parsed) == phonenumbers.PhoneNumberType.MOBILE:
            legacy = modern[:4] + modern[5:]
            return tuple(dict.fromkeys((number, modern, legacy)))
    return (number,)


def search_key(value):
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)).casefold()


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


def match_patient_name(match, name):
    """Disambiguate a shared full phone only with an exact, complete source name."""
    key = " ".join(search_key(str(name)).split())
    if match["status"] != "review" or sum(any(ch.isalpha() for ch in part) for part in key.split()) < 2:
        return match
    candidates = [p for p in match.get("candidates", [])
                  if " ".join(search_key(p["name"]).split()) == key]
    if len(candidates) != 1:
        return match
    return {"status": "matched", **candidates[0], "synced_at": match.get("synced_at", 0),
            "match_basis": "phone_name"}


class PatientDirectory:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.checked = 0
        self.available = False
        self.index = {}
        self.patients = []
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
            index, patients = {}, []
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
                patient = {"uuid": str(uid), "name": str(name or raw.get("name") or "").strip()[:160]}
                numbers = list(dict.fromkeys(filter(None, map(experts_phone, values))))
                for number in numbers:
                    index.setdefault(number, {})[str(uid)] = patient
                patients.append({**patient, "numbers": numbers})
                synced = max(synced, int(updated or 0))
            self.index, self.patients, self.available, self.synced_at = index, patients, bool(rows), synced
        except (sqlite3.Error, OSError, TypeError, ValueError):
            self.available = False

    def lookup(self, number):
        # Never match by the last 8/9 digits: identical numbers in other DDDs
        # are different people. Multiple patients sharing a phone need review.
        with self.lock:
            self.refresh()
            if not self.available:
                return {"status": "unchecked", "synced_at": self.synced_at}
            keys = phone_keys(number)
            candidates = list({uid: p for key in keys for uid, p in self.index.get(key, {}).items()}.values())
            if len(candidates) == 1 and candidates[0]["name"]:
                return {"status": "matched", **candidates[0], "synced_at": self.synced_at,
                        "match_basis": "phone" if candidates[0]["uuid"] in self.index.get(keys[0], {}) else "phone_variant"}
            return {"status": "review" if candidates else "new", "candidates": candidates, "synced_at": self.synced_at}

    def invalidate(self):
        with self.lock:
            self.checked = 0

    def search(self, query="", page=1):
        query = search_key(str(query)[:160].strip())
        digits = re.sub(r"\D", "", query)
        page = max(1, int(page))
        with self.lock:
            self.refresh()
            if not self.available:
                return {"available": False, "items": [], "page": 1, "pages": 1, "total": 0, "synced_at": self.synced_at}
            matches = [p for p in self.patients if not query or query in search_key(p["name"]) or
                       (digits and any(digits in key for number in p["numbers"] for key in phone_keys(number)))]
            matches.sort(key=lambda p: (search_key(p["name"]), p["uuid"]))
            pages = max(1, (len(matches) + 24) // 25)
            page = min(page, pages)
            def owners(number):
                return {uid for key in phone_keys(number) for uid in self.index.get(key, {})}
            items = [{"uuid": p["uuid"], "name": p["name"],
                      "phones": [number for number in p["numbers"] if len(owners(number)) == 1],
                      "shared_phone": any(len(owners(number)) > 1 for number in p["numbers"])}
                     for p in matches[(page - 1) * 25:page * 25]]
            return {"available": True, "items": items, "page": page, "pages": pages,
                    "total": len(matches), "synced_at": self.synced_at}
