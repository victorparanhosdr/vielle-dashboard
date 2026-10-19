"""Read-only, UUID-linked appointment summaries and history for the CRM."""
from datetime import datetime
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from financial_receipts import CANCELLED, normalized, record

TZ = ZoneInfo("America/Sao_Paulo")
DELETED = {"deleted", "removed", "excluido", "excluida"}
COMPLETED = {"done", "completed", "finished", "executed", "attended", "realizado", "realizada", "concluido", "concluida"}
MISSED = {"no_show", "missed", "absent", "not_attended", "faltou"}
FINISHED = COMPLETED | MISSED
LABELS = {"scheduled": "Agendado", "active": "Agendado", "open": "Agendado", "pending": "Pendente", "unconfirmed": "Não confirmado",
          "confirmed": "Confirmado", "arrived": "Chegou", "waiting": "Aguardando", "in_progress": "Em atendimento",
          "done": "Concluído", "completed": "Concluído", "finished": "Concluído", "executed": "Realizado", "attended": "Realizado",
          "no_show": "Não compareceu", "missed": "Não compareceu", "absent": "Não compareceu", "not_attended": "Não compareceu"}


def moment(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)
    except ValueError:
        return None


class PatientAppointments:
    def __init__(self, path, professionals=None, now=None):
        self.path = Path(path)
        self.professionals = {normalized(name): str(uid) for name, uid in (professionals or {}).items()}
        self.now = now or (lambda: datetime.now(TZ))

    def load(self, patients, doctor, scope="doctor"):
        patients = list(dict.fromkeys(patients))
        results = {uid: {"state": "unavailable", "items": []} for uid in patients}
        if not patients or not self.path.exists():
            return results
        try:
            c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=3)
            c.row_factory = sqlite3.Row
            try:
                c.execute("BEGIN")
                synced = c.execute("SELECT max(synced_at) FROM clinica_bookings").fetchone()[0]
                if synced is None:
                    return results
                try:
                    catalog = {r["uuid"]: r["name"] for r in c.execute("SELECT uuid,name FROM clinica_procedures")}
                except sqlite3.OperationalError:
                    catalog = {}
                now = self.now().astimezone(TZ)
                for offset in range(0, len(patients), 400):
                    ids = patients[offset:offset+400];marks = ",".join("?" for _ in ids)
                    allowed = set()
                    for r in c.execute(f"SELECT uuid,raw_json FROM clinica_patients WHERE uuid IN ({marks})", ids):
                        raw = record(r["raw_json"])
                        if raw.get("deleted") is not True and not raw.get("deleted_at") and normalized(raw.get("status")) not in DELETED:
                            allowed.add(r["uuid"])
                    for uid in ids:
                        results[uid] = {"state": "ready" if uid in allowed else "identity_pending", "items": [], "synced_at": synced,
                                        "warnings": {"identity": 0, "professional": 0, "date": 0}}
                    rows = c.execute(f"""SELECT uuid,patient_uuid,professional_uuid,procedure_uuid,status,starts_at,ends_at,raw_json,synced_at
                        FROM clinica_bookings WHERE patient_uuid IN ({marks})""", ids)
                    for row in rows:
                        uid = row["patient_uuid"]
                        if uid not in allowed:
                            continue
                        result = results[uid];raw = record(row["raw_json"])
                        statuses = {normalized(row["status"]), normalized(raw.get("status"))}
                        if raw.get("deleted") is True or raw.get("deleted_at") or statuses & DELETED:
                            continue
                        patient = record(raw.get("patient"))
                        patient_id = patient.get("uuid") or patient.get("id") or raw.get("patient_uuid")
                        if patient_id and str(patient_id) != uid:
                            result["warnings"]["identity"] += 1;continue
                        professional = record(raw.get("professional"))
                        raw_owner = professional.get("uuid") or professional.get("id") or raw.get("professional_uuid")
                        owner = row["professional_uuid"] or raw_owner
                        name = professional.get("name") or professional.get("full_name") or professional.get("title") or raw.get("professional_name") or ""
                        if raw_owner and row["professional_uuid"] and str(raw_owner) != str(row["professional_uuid"]):
                            result["warnings"]["professional"] += 1;continue
                        if scope == "doctor":
                            expected = self.professionals.get(normalized(doctor))
                            if owner and expected:
                                if str(owner) != expected:
                                    continue
                            elif normalized(name) != normalized(doctor):
                                if not name:
                                    result["warnings"]["professional"] += 1
                                continue
                        if statuses & COMPLETED and statuses & MISSED:
                            result["warnings"]["status"] = result["warnings"].get("status", 0)+1
                            continue
                        start_raw = row["starts_at"] or raw.get("starts_at") or raw.get("start_at") or raw.get("scheduled_at")
                        start = moment(start_raw)
                        if start is None:
                            result["warnings"]["date"] += 1;continue
                        has_time = not bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(start_raw).strip()))
                        end = moment(row["ends_at"] or raw.get("ends_at") or raw.get("end_at"))
                        end = end if end and end > start else None
                        future = start >= now if has_time else start.date() >= now.date()
                        cancelled = bool(statuses & CANCELLED) or raw.get("active") is False
                        finished = bool(statuses & FINISHED)
                        finished_in_future = start > now if has_time else start.date() > now.date()
                        if finished and finished_in_future and not cancelled:
                            result["warnings"]["date"] += 1;continue
                        phase = "cancelled" if cancelled else "past" if finished else "ongoing" if has_time and start <= now and end and end > now else "upcoming" if future else "past"
                        procedure = record(raw.get("procedure"))
                        procedure_name = procedure.get("name") or procedure.get("title") or catalog.get(row["procedure_uuid"]) or raw.get("procedure_name") or "Procedimento não informado"
                        status = normalized(row["status"] or raw.get("status"))
                        result["items"].append({"uuid": row["uuid"], "starts_at": start.timestamp(), "ends_at": end.timestamp() if end else None,
                            "date": start.date().isoformat(), "time": start.strftime("%H:%M") if has_time else None, "phase": phase,
                            "attendance": "completed" if statuses & COMPLETED else "missed" if statuses & MISSED else "unknown",
                            "status": "cancelled" if cancelled else status, "status_label": "Cancelado" if cancelled else LABELS.get(status, str(row["status"] or raw.get("status") or "Status não informado")[:80]),
                            "procedure": str(procedure_name)[:200], "professional": str(name or (doctor if scope == "doctor" else "Profissional não informado"))[:160]})
                return results
            finally:
                c.close()
        except (sqlite3.Error, OSError):
            return {uid: {"state": "unavailable", "items": []} for uid in patients}

    @staticmethod
    def summary(data):
        public = {k: v for k, v in data.items() if k != "items"}
        if data["state"] != "ready":
            return {**public, "item": None, "kind": None}
        upcoming = sorted((r for r in data["items"] if r["phase"] in {"upcoming", "ongoing"}), key=lambda r: r["starts_at"])
        past = sorted((r for r in data["items"] if r["phase"] == "past"), key=lambda r: r["starts_at"], reverse=True)
        item = (upcoming or past or [None])[0]
        return {**public, "item": item, "kind": "ongoing" if item and item["phase"] == "ongoing" else "next" if upcoming else "last" if past else None}

    def summaries(self, patients, doctor):
        return {uid: self.summary(data) for uid, data in self.load(patients, doctor).items()}

    def segments(self, patients, doctor, status="", start="", end=""):
        results = {}
        for uid, data in self.load(patients, doctor).items():
            summary = self.summary(data)
            values = [r for r in data["items"] if r["phase"] != "cancelled"]
            upcoming = any(r["phase"] in {"upcoming", "ongoing"} for r in values)
            values = [r for r in values if (not start or r["date"] >= start) and (not end or r["date"] <= end)]
            if status in {"completed", "missed"}:
                values = [r for r in values if r["attendance"] == status]
            elif status == "scheduled":
                values = [r for r in values if r["phase"] in {"upcoming", "ongoing"}]
            values.sort(key=lambda r: (r["starts_at"], r["uuid"]), reverse=status != "scheduled")
            state = "partial" if data["state"] == "ready" and any(data.get("warnings", {}).values()) else data["state"]
            results[uid] = {"state": state, "count": len(values), "has_upcoming": upcoming,
                            "item": values[0] if values else None,
                            "kind": status if status in {"completed", "scheduled", "missed"} else "matched",
                            "summary": summary}
        return results

    def history(self, patient, doctor, scope, period, page):
        data = self.load([patient], doctor, scope)[patient]
        summary = self.summary(data)
        items = data["items"]
        now = self.now().astimezone(TZ).timestamp()
        if period == "upcoming":
            items = [r for r in items if r["phase"] in {"upcoming", "ongoing"}]
        elif period == "past":
            items = [r for r in items if r["phase"] == "past" or (r["phase"] == "cancelled" and r["starts_at"] < now)]
        items.sort(key=lambda r: (0 if r["phase"] == "ongoing" else 1 if r["phase"] == "upcoming" else 2,
                                  r["starts_at"] if r["starts_at"] >= now else -r["starts_at"], r["uuid"]))
        pages = max(1, (len(items)+24)//25);page = min(page, pages)
        return {**summary, "scope": scope, "period": period, "page": page, "pages": pages, "total": len(items), "doctor_name": doctor,
                "items": items[(page-1)*25:page*25]}
