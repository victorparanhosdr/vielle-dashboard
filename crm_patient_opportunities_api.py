"""Session/clinic/CRM scope is enforced by the normal CRM dispatcher."""
import json

import crm_patient_opportunities as opportunities
from task_api import read_body


def handle(handler, store, query, user):
    doctor = query.get("doctor", [""])[0]
    if handler.command == "GET":
        return handler.auth_json({"ok": True, **opportunities.listing(store, doctor, query.get("lead", [""])[0])})
    if handler.command != "POST":
        return handler.auth_json({"ok": False}, 405)
    if not handler.same_origin_request():
        return handler.auth_json({"ok": False}, 403)
    payload = json.loads(read_body(handler, 16*1024))
    if not isinstance(payload, dict) or payload.get("doctor_id") != doctor:
        raise ValueError("Profissional inválido para esta oportunidade.")
    if not isinstance(payload.get("lead_id"), str):
        raise ValueError("Paciente inválido.")
    ident = opportunities.save(store, doctor, payload, user)
    handler.server.auth_store.audit_event("crm_patient_opportunity_saved", user["id"],
        details={"clinic": "vielle", "doctor_id": doctor, "id": ident})
    return handler.auth_json({"ok": True, "id": ident})
