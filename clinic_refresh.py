"""Deduplicated, non-destructive refresh jobs scoped to an authorized clinic."""
import copy
import math
import threading
import time
import uuid


class ClinicRefreshJobs:
    def __init__(self, cooldown=60):
        self.lock = threading.Lock()
        self.jobs = {}
        self.cooldown = cooldown

    def _snapshot(self, clinic):
        job = copy.deepcopy(self.jobs.get(clinic, {
            "running": False, "phase": "idle", "job_id": None, "message": "", "services": [],
        }))
        deadline = job.pop("cooldown_until", 0)
        job["retry_after"] = max(0, math.ceil(deadline - time.monotonic()))
        return {"ok": True, "clinic": clinic, **job}

    def status(self, clinic):
        with self.lock:
            return self._snapshot(clinic)

    def start(self, clinic, run, *, background=True, source="manual"):
        with self.lock:
            current = self._snapshot(clinic)
            if current["running"] or current["retry_after"]:
                return {**current, "started": False}
            self.jobs[clinic] = {
                "job_id": uuid.uuid4().hex, "running": True, "phase": "running",
                "message": "Atualização iniciada.", "services": [],
                "started_at": int(time.time()), "finished_at": None,
                "source": source,
            }
            accepted = {**self._snapshot(clinic), "started": True}

        def progress(name):
            with self.lock:
                self.jobs[clinic]["message"] = f"Atualizando {name}..."

        def runner():
            try:
                services = run(progress)
                good = any(item["status"] in {"done", "warning"} for item in services)
                issues = any(item["status"] in {"error", "warning"} for item in services)
                phase = "partial" if good and issues else "done" if good else "error" if issues else "empty"
                labels = {"done": "atualizado", "warning": "atualizado com pendências",
                          "error": "falhou; consulte o Master", "skipped": "não conectado"}
                message = "; ".join(f"{item['name']}: {labels[item['status']]}" for item in services) + "."
            except Exception:
                services, phase = [], "error"
                message = "Não foi possível concluir a atualização. Consulte o Master."
            with self.lock:
                self.jobs[clinic].update(running=False, phase=phase, services=services,
                    message=message, finished_at=int(time.time()),
                    cooldown_until=time.monotonic() + self.cooldown)

        try:
            if background:
                threading.Thread(target=runner, daemon=True, name=f"refresh-{clinic}").start()
            else:
                runner()
        except Exception:
            with self.lock:
                self.jobs[clinic].update(running=False, phase="error", finished_at=int(time.time()),
                    message="Não foi possível iniciar a atualização. Tente novamente.")
            raise
        return accepted if background else {**self.status(clinic), "started": True}
