"""Explicit patient-directory refresh; no financial sync or writes to Experts."""
import threading
import time

from clinic_refresh import ClinicRefreshJobs

JOBS, LOCK = {}, threading.Lock()


def jobs_for(store):
    with LOCK:
        return JOBS.setdefault(store.path, ClinicRefreshJobs(cooldown=10))


def patient_snapshot(request, extract, max_pages=100):
    patients = {}
    for page in range(1, max_pages + 1):
        payload = request(f"/patients?per_page=100&page={page}")
        valid = isinstance(payload, list) or isinstance(payload, dict) and any(
            isinstance(payload.get(key), list) for key in ("data", "patients"))
        if not valid:
            raise ValueError("Formato de pacientes nao confirmado.")
        items = extract(payload, ["data", "patients"])
        new_ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Paciente invalido.")
            uid = item.get("uuid") or item.get("id") or item.get("patient_uuid")
            if not uid or not (item.get("name") or item.get("full_name")):
                raise ValueError("Identificacao de paciente incompleta.")
            uid = str(uid)
            if uid not in patients:
                new_ids.add(uid)
            patients[uid] = item
        if items and not new_ids:
            raise ValueError("Paginacao de pacientes repetida.")
        if len(items) < 100:
            if not patients:
                raise ValueError("A API nao confirmou uma base de pacientes.")
            return list(patients.values())
    raise ValueError("Busca de pacientes incompleta.")


def sync_patients(store, progress):
    import app

    @app.CLINIC_SYNC_LOCKS.serialize(lambda: "vielle")
    def run():
        with app.clinic_context("vielle"):
            if not app.configured(app.config_value("CLINICA_EXPERTS_TOKEN", "")):
                raise ValueError("Clinica Experts nao configurado.")
            progress("cadastros do Clinica Experts")
            # Stage the complete snapshot before changing the local cache.
            patients = patient_snapshot(app.clinica_request, app.extract_items)
            with app.db() as conn:
                for patient in patients:
                    app.save_clinica_patient(conn, patient, int(time.time()))
            store.patient_directory.invalidate()
        return [{"name": "Clinica Experts - pacientes", "status": "done"}]

    return run()


def start_refresh(store):
    if store.patient_directory is None:
        raise ValueError("Atualizacao de pacientes indisponivel neste ambiente.")
    return jobs_for(store).start("vielle", lambda progress: sync_patients(store, progress))


def status(store):
    return jobs_for(store).status("vielle")
