"""Master-only migration; previews never mutate patient cards."""
import json

import crm_clinic_journey as journey
from task_api import read_body


def handle(handler, store, action, query, user):
    if not user.get("is_master"):
        return handler.auth_json({"ok": False, "error": "Configuração exclusiva do Master."}, 403)
    if handler.command != "POST":
        return handler.auth_json({"ok": False}, 405)
    if not handler.same_origin_request():
        return handler.auth_json({"ok": False}, 403)
    payload = json.loads(read_body(handler, 8192))
    doctor = query.get("doctor", [""])[0]
    if not isinstance(payload, dict) or payload.get("doctor_id") != doctor:
        raise ValueError("Profissional inválido.")
    if not isinstance(payload.get("pipeline_id") if action.endswith("preview") else payload.get("id"), str):
        raise ValueError("Identificador inválido.")
    if action == "clinic-journey/preview":
        result = journey.preview(store, doctor, payload.get("pipeline_id"), user)
    elif action == "clinic-journey/apply":
        result = journey.apply(store, doctor, payload, user)
        handler.server.auth_store.audit_event("crm_clinic_journey_applied", user["id"],
            details={"pipeline_id": result["pipeline_id"], "count": result["count"]})
    else:
        raise LookupError()
    return handler.auth_json({"ok": True, **result})
