"""User administration for authenticated Master sessions only."""

import json
import re
import sqlite3
from urllib.parse import parse_qs

from auth_store import normalize_login, password_bytes
from clinic_catalog import CLINIC_DISPLAY_NAMES, validate_clinic_keys
from access_policy import MODULES, ACTION_LABELS, permission_map


def user_fields(data, fields):
    if set(data) - set(fields):
        raise ValueError("Campos não permitidos nesta operação.")
    return data


def validate_identity(data):
    name = data.get("nome")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
        raise ValueError("Informe um nome com até 200 caracteres.")
    try:
        login = normalize_login(data.get("login"))
    except ValueError:
        raise ValueError("Informe um login válido, sem espaços e com até 254 caracteres.")
    return name.strip(), login


def validate_password(data):
    value = data.get("password")
    try:
        password_bytes(value, new=True)
    except (ValueError, UnicodeError):
        raise ValueError("A senha precisa ter pelo menos 12 caracteres e no máximo 1024 bytes.")
    return value


class MasterApiMixin:
    def handle_master_api(self, parsed):
        if not (parsed.path == "/api/master" or parsed.path.startswith("/api/master/")):
            return False
        if not self.require_master_auth():
            return True
        store = self.server.auth_store
        actor_id = self.current_user["id"]
        try:
            if self.command == "GET" and parsed.path == "/api/master/audit":
                query = parse_qs(parsed.query)
                self.auth_json({"ok": True, **store.list_audit(int(query.get("page", ["1"])[0]), query.get("action", [""])[0])})
                return True
            if self.command == "GET" and parsed.path == "/api/master/profiles":
                self.auth_json({"ok": True, "profiles": store.list_profiles(), "modules": MODULES, "actions": ACTION_LABELS})
                return True
            if self.command == "GET" and parsed.path == "/api/master/users":
                query = parse_qs(parsed.query)
                status = query.get("status", [""])[0]
                search = query.get("search", [""])[0]
                page = int(query.get("page", ["1"])[0])
                if status not in ("", "active", "inactive") or not 1 <= page <= 1_000_000:
                    raise ValueError("Filtro inválido.")
                self.auth_json({"ok": True, **store.list_users(search, status, page),
                                "clinics": [{"key": key, "name": name} for key, name in CLINIC_DISPLAY_NAMES.items()],
                                "modules": MODULES, "actions": ACTION_LABELS, "profiles": store.list_profiles()})
                return True
            match = re.fullmatch(r"/api/master/users/([1-9][0-9]{0,17})/(edit|status|password)", parsed.path)
            profile_match = re.fullmatch(r"/api/master/profiles/([1-9][0-9]{0,17})", parsed.path)
            profile_route = parsed.path == "/api/master/profiles" or bool(profile_match)
            if self.command != "POST" or not (parsed.path == "/api/master/users" or match or profile_route):
                self.auth_json({"ok": False, "error": "Rota não encontrada."}, 404)
                return True
            if not self.same_origin_request():
                self.auth_json({"ok": False, "error": "Origem da solicitação inválida."}, 403)
                return True
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get_content_type() != "application/json":
                raise ValueError("Envie os dados em JSON válido.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Envie os dados em JSON válido.")
            if profile_route:
                user_fields(data, ("name", "permissions"))
                profile_id = store.save_profile(data.get("name"), data.get("permissions"), int(profile_match[1]) if profile_match else None, actor_id)
                self.auth_json({"ok": True, "id": profile_id})
                return True
            if not match:
                user_fields(data, ("nome", "login", "password", "status", "clinic_keys", "permissions"))
                name, login = validate_identity(data)
                password = validate_password(data)
                status = data.get("status", "active")
                if status not in ("active", "inactive"):
                    raise ValueError("Status inválido.")
                keys = validate_clinic_keys(data.get("clinic_keys", []))
                if "permissions" in data and not isinstance(data["permissions"], dict):
                    raise ValueError("Permissões inválidas.")
                permissions = permission_map(keys, data.get("permissions", {key: [] for key in keys}))
                user_id = store.create_user(name, login, password, status=status, clinic_keys=keys, permissions=permissions, actor_id=actor_id)
                self.auth_json({"ok": True, "user": store.get_public_user(user_id)}, 201)
                return True
            user_id, action = int(match[1]), match[2]
            if not store.get_public_user(user_id):
                self.auth_json({"ok": False, "error": "Usuário não encontrado."}, 404)
                return True
            if action == "edit":
                user_fields(data, ("nome", "login", "clinic_keys", "permissions"))
                name, login = validate_identity(data)
                if "clinic_keys" in data and not isinstance(data["clinic_keys"], list):
                    raise ValueError("Clínicas inválidas.")
                if "permissions" in data and not isinstance(data["permissions"], dict):
                    raise ValueError("Permissões inválidas.")
                store.update_user(user_id, name, login, clinic_keys=data.get("clinic_keys"), permissions=data.get("permissions"), actor_id=actor_id)
            elif action == "status":
                user_fields(data, ("active",))
                if not isinstance(data.get("active"), bool):
                    raise ValueError("Status inválido.")
                store.set_user_active(user_id, data["active"], actor_id=actor_id)
            else:
                user_fields(data, ("password",))
                store.set_password(user_id, validate_password(data), actor_id=actor_id)
            self.auth_json({"ok": True, "user": store.get_public_user(user_id)})
        except sqlite3.IntegrityError:
            self.auth_json({"ok": False, "error": "Este login ou nome de perfil já está em uso."}, 409)
        except (ValueError, UnicodeError) as error:
            message = str(error) if not isinstance(error, (json.JSONDecodeError, UnicodeError)) else "JSON inválido."
            self.auth_json({"ok": False, "error": message}, 400)
        except sqlite3.OperationalError:
            self.auth_json({"ok": False, "error": "Banco temporariamente indisponível. Tente novamente."}, 503)
        return True
