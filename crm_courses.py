"""Course-only Kiwify enrichment. Never reads or writes clinical records."""
from collections import Counter
from datetime import datetime
import threading
import time
from zoneinfo import ZoneInfo

from crm_patients import experts_phone
from institute_report import email, PAID, STATUS_LABELS
from institute_sources import Kiwify, SourceError

PENDING = {"waiting_payment", "pending", "processing", "authorized"}
REFUNDED = {"refunded", "chargedback"}
JOBS, LOCK = {}, threading.Lock()


class CourseCommercial:
    def __init__(self, store):
        self.store = store
        self.cache, self.lock = {}, threading.Lock()

    def snapshot(self, institute, course):
        info = self.store.course(institute, course)
        if not info:
            return [], {"ok": False}
        products = set(info["product_ids"])
        sync = self.store.sync_state(institute, course).get("kiwify", {})
        key = (institute, course, tuple(sorted(products)), sync.get("at"), sync.get("attempt_at"))
        with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 15:
                return cached[1], sync
        rows = [r for r in self.store.records(institute, course, "kiwify") if r.get("product_id") in products]
        with self.lock:
            self.cache[key] = (time.monotonic(), rows)
            if len(self.cache) > 30:
                self.cache = {key: self.cache[key]}
        return rows, sync

    def match(self, institute, course, emails, phones):
        rows, sync = self.snapshot(institute, course)
        emails = set(filter(None, map(email, emails)))
        phones = set(filter(None, map(experts_phone, phones)))
        exact = [r for r in rows if email(r.get("email")) in emails]
        by_phone = [r for r in rows if experts_phone(r.get("phone")) in phones]
        identities = {email(r.get("email")) for r in by_phone if email(r.get("email"))}
        ambiguous = not exact and (len(identities) > 1 or bool(emails and identities and not emails.intersection(identities)))
        selected = exact if exact else [] if ambiguous else by_phone
        return selected, sync, ambiguous

    def recover_phones(self, institute, course, emails):
        rows, _, ambiguous = self.match(institute, course, emails, [])
        numbers = list(dict.fromkeys(filter(None, (experts_phone(r.get("phone")) for r in rows))))
        return numbers if not ambiguous and len(numbers) == 1 else []

    def summary(self, institute, course, emails, phones):
        rows, sync, ambiguous = self.match(institute, course, emails, phones)
        state = "ambiguous" if ambiguous else "ready" if sync.get("ok") or sync.get("at") else "unavailable"
        orders, totals = [], Counter()
        invalid = net_missing = 0
        for row in {str(r["id"]): r for r in rows}.values():
            status = str(row.get("status", "unknown"))
            gross, net = row.get("gross"), row.get("net")
            valid = type(gross) is int and gross >= 0 and row.get("currency") == "BRL"
            if not valid:
                invalid += 1
            if status in PAID:
                totals["paid_count"] += 1
                if valid:
                    totals["paid_cents"] += gross
                if type(net) is int and row.get("net_currency") == "BRL":
                    totals["net_cents"] += net
                else:
                    net_missing += 1
            elif status in PENDING:
                totals["pending_count"] += 1
                if valid:
                    totals["pending_cents"] += gross
            elif status in REFUNDED:
                totals["refunded_count"] += 1
                if valid:
                    totals["refunded_cents"] += gross
            orders.append({"id": str(row["id"]), "status": status, "status_label": STATUS_LABELS.get(status, status),
                           "date": row.get("approved_day") if status in PAID else row.get("refunded_day") or row.get("created_day", ""),
                           "gross_cents": gross if valid else None, "net_cents": net if type(net) is int and row.get("net_currency") == "BRL" else None,
                           "payment_method": str(row.get("payment_method", "")), "email": email(row.get("email"))})
        orders.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
        return {"state": state, "source": "Kiwify", "institute_key": institute, "course_key": course,
                "synced_at": sync.get("at", 0), "sync_ok": sync.get("ok", False), "partial": bool(invalid), "net_complete": not net_missing,
                **{key: totals[key] for key in ("paid_count", "paid_cents", "net_cents", "pending_count", "pending_cents", "refunded_count", "refunded_cents")},
                "orders": orders[:100], "order_count": len(orders)}


def refresh(source, institute, course):
    info = source.store.course(institute, course)
    if not info:
        raise ValueError("Curso nao encontrado no instituto.")
    end = datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()
    try:
        rows = Kiwify(source.store.settings(institute)).sales(info, "2025-01-01", end)
    except Exception:
        previous = source.store.sync_state(institute, course).get("kiwify", {})
        source.store.set_sync_state(institute, course, "kiwify", {**previous, "ok": False, "attempt_at": int(time.time()),
                                                               "error": "Atualizacao Kiwify nao concluida; base anterior preservada."})
        raise
    source.store.save_records(institute, course, "kiwify", rows, window=("created_day", "2025-01-01", end))
    source.store.set_sync_state(institute, course, "kiwify", {"ok": True, "count": len(rows), "at": int(time.time()), "from": "2025-01-01", "to": end})
    return len(rows)


def start_refresh(source, institute, course):
    key = (str(source.store.path), institute, course)
    with LOCK:
        current = JOBS.get(key, {})
        if current.get("running") or time.time() - current.get("started_at", 0) < 60:
            return dict(current)
        JOBS[key] = {"running": True, "started_at": time.time(), "message": "Atualizando pedidos Kiwify..."}
    def run():
        try:
            count = refresh(source, institute, course)
            result = {"running": False, "message": f"Kiwify atualizada: {count} pedido(s).", "ok": True}
        except Exception as exc:
            result = {"running": False, "message": str(exc) if isinstance(exc, SourceError) else "Nao foi possivel atualizar a Kiwify. Os dados anteriores foram preservados.", "ok": False}
        with LOCK:
            JOBS[key] = {**JOBS[key], **result}
    threading.Thread(target=run, daemon=True, name="crm-kiwify-refresh").start()
    return {"running": True, "message": "Atualizando pedidos Kiwify..."}


def refresh_status(source, institute, course):
    with LOCK:
        return dict(JOBS.get((str(source.store.path), institute, course), {"running": False, "message": ""}))
