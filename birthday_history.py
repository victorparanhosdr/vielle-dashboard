"""Resumable, clinic-scoped patient and sales backfill starting in 2024."""
from datetime import datetime
import time

from clinic_refresh import ClinicRefreshJobs
from periodic_sync import BRAZIL


START = "2024-01-01"
JOBS = ClinicRefreshJobs()


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS birthday_history_months (
        month TEXT PRIMARY KEY, through_date TEXT NOT NULL,
        completed_at INTEGER NOT NULL, records INTEGER NOT NULL
    )""")


def status(conn, clinic, reference=None):
    from app import config_value, configured, month_ranges
    reference = reference or datetime.now(BRAZIL).date()
    initialize(conn)
    months = list(month_ranges(START, reference.isoformat()))
    coverage = {r["month"]: dict(r) for r in conn.execute("SELECT * FROM birthday_history_months")}
    completed = sum(coverage.get(start[:7], {}).get("through_date", "") >= end for start, end in months)
    return {**JOBS.status(clinic), "connected": configured(config_value("CLINICA_EXPERTS_TOKEN", "")),
            "from": START, "through": reference.isoformat(), "completed_months": completed,
            "total_months": len(months), "complete": completed == len(months)}


def fetch_pages(path, keys, saver, seen=None, max_pages=500):
    from app import clinica_request, db, first_value
    seen = seen if seen is not None else set()
    page_ids, added = set(), 0
    for page in range(1, max_pages + 1):
        payload = clinica_request(f"{path}{'&' if '?' in path else '?'}page={page}")
        items = payload if isinstance(payload, list) else next(
            (payload[key] for key in keys if isinstance(payload, dict) and isinstance(payload.get(key), list)), None)
        if items is None or any(not isinstance(item, dict) for item in items):
            raise RuntimeError("Formato inesperado no histórico do Clínica Experts.")
        meta = payload.get("meta", payload) if isinstance(payload, dict) else {}
        last_page = meta.get("last_page") if isinstance(meta, dict) else None
        if last_page is not None and (type(last_page) is not int or last_page < 0 or last_page > max_pages):
            raise RuntimeError("Paginação incompleta no histórico do Clínica Experts.")
        if last_page == 0 and items:
            raise RuntimeError("Paginação incompatível com os registros recebidos.")
        ids = [first_value(item, ["uuid", "id", "sale_uuid", "patient_uuid"]) for item in items]
        if any(not value for value in ids):
            raise RuntimeError("Registro sem identificador no histórico do Clínica Experts.")
        ids = {str(value) for value in ids}
        if items and page > 1 and not ids - page_ids:
            raise RuntimeError("A API repetiu uma página do histórico.")
        if not items and last_page is not None and last_page > page:
            raise RuntimeError("A API retornou uma página vazia antes do fim.")
        with db() as conn:
            for item in items:
                if not saver(conn, item, int(time.time())):
                    raise RuntimeError("Não foi possível salvar um registro do histórico.")
        added += len(ids - seen)
        seen.update(ids)
        page_ids.update(ids)
        if (last_page is not None and page >= last_page) or (last_page is None and len(items) < 100):
            return added
    raise RuntimeError("O histórico excedeu o limite de páginas; cobertura não confirmada.")


def sync_month(start, end):
    from app import save_clinica_sale
    seen, total = set(), 0
    for column in ("sale_date", "created_at", "updated_at"):
        path = (f"/sales?starts_at={start}T00:00:00-03:00&ends_at={end}T23:59:59-03:00"
                f"&sort_column={column}&per_page=100")
        total += fetch_pages(path, ["data", "sales"], save_clinica_sale, seen)
    return total


def run(clinic, progress, reference=None):
    import app
    reference = reference or datetime.now(BRAZIL).date()
    with app.clinic_context(clinic):
        @app.CLINIC_SYNC_LOCKS.serialize(app.current_clinic_id)
        def backfill():
            progress("cadastros de pacientes, sem filtro de ano")
            fetch_pages("/patients?per_page=100", ["data", "patients"], app.save_clinica_patient)
            for start, end in app.month_ranges(START, reference.isoformat()):
                with app.db() as conn:
                    initialize(conn)
                    row = conn.execute("SELECT through_date FROM birthday_history_months WHERE month=?", (start[:7],)).fetchone()
                if row and row["through_date"] >= end:
                    continue
                progress(f"vendas de {start[5:7]}/{start[:4]}")
                total = sync_month(start, end)
                # Checkpoint only after every page and date variant succeeds.
                with app.db() as conn:
                    conn.execute("""INSERT INTO birthday_history_months VALUES(?,?,?,?)
                        ON CONFLICT(month) DO UPDATE SET through_date=excluded.through_date,
                        completed_at=excluded.completed_at, records=excluded.records""",
                        (start[:7], end, int(time.time()), total))
            return [{"name": "Histórico desde 2024", "status": "done"}]
        return backfill()


def start(clinic):
    return JOBS.start(clinic, lambda progress: run(clinic, progress))
