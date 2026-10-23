"""Scoped follow-up editing and explicit activation, without immediate sends."""
import json

import crm_followup_settings as settings
from task_api import read_body


def handle(handler, store, action, query, user):
    doctor = query.get("doctor", [""])[0]
    if handler.command == "GET" and action == "followups":
        result = settings.snapshot(store, doctor, query.get("pipeline", [""])[0])
        return handler.auth_json({"ok": True, "can_manage": bool(user.get("is_master")), **result})
    if handler.command != "POST":
        return handler.auth_json({"ok": False}, 405)
    if not user.get("is_master"):
        raise PermissionError("Somente o Master pode editar a régua de follow-up.")
    if not handler.same_origin_request():
        return handler.auth_json({"ok": False}, 403)
    payload = json.loads(read_body(handler, 256*1024))
    if not isinstance(payload, dict) or payload.get("doctor_id") != doctor:
        raise ValueError("Profissional inválido para a régua.")
    if action == "followups/settings":
        result = settings.save(store, doctor, payload, user)
        handler.server.auth_store.audit_event("crm_followup_draft_saved", user["id"],
            details={"doctor_id": doctor, "pipeline_id": result["pipeline_id"], "revision": result["revision"]})
    elif action == "followups/toggle":
        result=settings.toggle(store,doctor,payload,user)
        handler.server.auth_store.audit_event("crm_followup_toggled",user["id"],
            details={"doctor_id":doctor,"pipeline_id":result["pipeline_id"],"enabled":result["enabled"]})
    elif action == "followups/preview":
        result = settings.preview(store, doctor, payload)
    else:
        raise LookupError()
    return handler.auth_json({"ok": True, "can_manage": True, **result})
