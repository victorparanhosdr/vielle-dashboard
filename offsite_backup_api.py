"""Master-only operational status. No archive or private recovery key is exposed."""
import json

from offsite_backup import BackupError
from offsite_backup_runtime import overview, request_retry


def handle(handler, parsed):
    if parsed.path != "/api/master/backups":
        return False
    if not handler.require_master_auth():
        return True
    root = handler.server.auth_store.path.parent
    try:
        if handler.command == "GET":
            handler.auth_json({"ok": True, **overview(root)})
        elif handler.command == "POST":
            if not handler.same_origin_request():
                handler.auth_json({"ok": False, "error": "Origem invalida."}, 403)
                return True
            length = int(handler.headers.get("Content-Length", "0"))
            if handler.headers.get_content_type() != "application/json" or not 0 < length <= 256:
                raise ValueError()
            payload = json.loads(handler.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {"confirm"} or payload["confirm"] is not True:
                raise ValueError()
            result = request_retry(root)
            handler.server.auth_store.audit_event("offsite_backup_requested", handler.current_user["id"])
            handler.auth_json({"ok": True, **result}, 202)
        else:
            handler.auth_json({"ok": False, "error": "Metodo indisponivel."}, 405)
    except (ValueError, UnicodeError):
        handler.auth_json({"ok": False, "error": "Confirme a solicitacao corretamente."}, 400)
    except BackupError:
        handler.auth_json({"ok": False, "error": "Backup externo indisponivel. Revise a configuracao do servidor."}, 409)
    except Exception:
        handler.auth_json({"ok": False, "error": "Nao foi possivel consultar o backup. Revise os registros do servidor."}, 503)
    return True
