"""Quality review data is manager-only, including direct API access."""
import json

import crm_quality as quality
from task_api import read_body


def provider_ready():
    from app import configured, config_value
    return configured(str(config_value("OPENAI_API_KEY", "") or "").strip())


def handle(handler, store, action, query, user):
    if not user.get("is_master"):
        raise PermissionError("Avaliações de atendimento são exclusivas do gestor Master.")
    doctor = query.get("doctor", [""])[0]
    if handler.command == "GET":
        if action == "quality":
            return handler.auth_json({"ok": True, "provider_ready": provider_ready(), **quality.listing(store, doctor,
                channel=query.get("channel", [""])[0], contact=query.get("contact", [""])[0], page=query.get("page", [1])[0])})
        if action == "quality/item":
            return handler.auth_json({"ok": True, "job": quality.detail(store, doctor, query.get("id", [""])[0])})
        raise LookupError()
    if handler.command != "POST":
        return handler.auth_json({"ok": False}, 405)
    if not handler.same_origin_request():
        return handler.auth_json({"ok": False}, 403)
    payload = json.loads(read_body(handler, 32*1024))
    if not isinstance(payload, dict) or payload.get("doctor_id") != doctor:
        raise ValueError("Profissional inválido para a avaliação.")
    if action == "quality/settings":
        result = {"settings": quality.save_settings(store, doctor, payload, user["id"])}
    elif action == "quality/analyze":
        if payload.get("confirm") is not True:
            raise ValueError("Confirme a análise e o consumo da API.")
        if not provider_ready():
            raise ValueError("Configure a API OpenAI da clínica antes de solicitar uma avaliação.")
        include_audio=payload.get("include_audio",False)
        if type(include_audio) is not bool or include_audio and payload.get("confirm_audio") is not True:
            raise ValueError("Confirme o envio dos áudios à OpenAI e o consumo de transcrição.")
        result = {"id": quality.request(store, doctor, payload.get("channel_id"), payload.get("contact_id"), user["id"],
            retry_from=payload.get("retry_from", ""), request_id=payload.get("request_id", ""),include_audio=include_audio)}
    elif action == "quality/review":
        quality.review(store, doctor, payload.get("id"), payload, user["id"])
        result = {}
    else:
        raise LookupError()
    handler.server.auth_store.audit_event("crm_" + action, user["id"], details={"clinic": "vielle", "doctor_id": doctor, "id": result.get("id", payload.get("id", ""))})
    return handler.auth_json({"ok": True, **result})
