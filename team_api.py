"""Same-clinic presence is public to coworkers; historical metrics require team.view."""

import json
from urllib.parse import parse_qs

from clinic_catalog import SUPPORTED_CLINICS
from task_api import read_body
from team_presence import TeamPresence


def handle_request(handler, parsed):
    query = parse_qs(parsed.query, keep_blank_values=True)
    clinic = query.get("clinic", [])
    auth, user = handler.server.auth_store, handler.current_user or {}
    if len(clinic) != 1 or clinic[0] not in SUPPORTED_CLINICS:
        return handler.auth_json({"ok": False, "error": "Informe uma clínica válida."}, 400)
    clinic = clinic[0]
    if not user.get("id") or not auth.user_can_access_clinic(user["id"], clinic):
        return handler.auth_json({"ok": False, "code": "clinic_forbidden", "error": "Sem acesso a esta clínica."}, 403)
    action = parsed.path.removeprefix("/api/team/")
    can_manage = auth.has_permission(user["id"], clinic, "team.view")
    store = TeamPresence(auth)
    if action == "report" and not can_manage:
        return handler.auth_json({"ok": False, "code": "permission_denied", "error": "Relatório exclusivo dos gestores autorizados."}, 403)
    try:
        if handler.command == "GET" and action == "presence":
            return handler.auth_json({"ok": True, "can_manage": can_manage, **store.presence(clinic, user["id"])})
        if handler.command == "GET" and action == "report":
            return handler.auth_json({"ok": True, **store.report(clinic, query)})
        if handler.command != "POST" or action not in {"heartbeat", "leave", "help", "help/action"}:
            return handler.auth_json({"ok": False, "error": "Rota ou método inválido."}, 405)
        if not handler.same_origin_request():
            return handler.auth_json({"ok": False, "error": "Origem inválida."}, 403)
        payload = json.loads(read_body(handler, 8192))
        if not isinstance(payload, dict):
            raise ValueError("Solicitação inválida.")
        if action in {"heartbeat", "leave"}:
            state = store.heartbeat(clinic, user["id"], handler.session_token(), payload, leave=action == "leave")
            return handler.auth_json({"ok": True, **state})
        if action == "help":
            return handler.auth_json({"ok": True, "id": store.help(clinic, user["id"], payload)}, 201)
        store.help_action(clinic, user["id"], payload)
        return handler.auth_json({"ok": True})
    except LookupError:
        return handler.auth_json({"ok": False, "error": "Registro não encontrado nesta clínica."}, 404)
    except PermissionError as exc:
        return handler.auth_json({"ok": False, "error": str(exc)}, 403)
    except (ValueError, TypeError, UnicodeError) as exc:
        return handler.auth_json({"ok": False, "error": str(exc) or "Solicitação inválida."}, 400)
