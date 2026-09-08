"""Session guard for the existing standard-library HTTP server."""

from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import posixpath
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from auth_store import SESSION_TTL_SECONDS, normalize_login
from clinic_catalog import SUPPORTED_CLINICS
from access_policy import VIEW_MODULES, clinic_modules


SESSION_COOKIE = "doc4docs_session"
PUBLIC_FILES = {"/login.html", "/login.css", "/login.js", "/session.js", "/doc4docs-logo-white.png"}
INTEGRATION_CALLBACKS = {"/auth/callback", "/webhooks/revoked"}
ADMIN_API_PATHS = {"/api/settings", "/api/sync-all", "/api/clear-data", "/api/reset-kommo", "/api/sync", "/api/sync-clinica", "/auth/start"}


def is_admin_path(path):
    path = posixpath.normpath(unquote(path))
    if path.startswith("/static/"):
        path = path.removeprefix("/static")
    return (path in ADMIN_API_PATHS or path in {"/master", "/master.html", "/settings.html", "/settings.js"}
            or path.startswith("/master/") or path == "/api/master" or path.startswith("/api/master/"))


class LoginLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.attempts = {}

    def allow(self, login, peer):
        try:
            login = normalize_login(login)
        except ValueError:
            login = "invalid"
        now = time.monotonic()
        with self.lock:
            self.attempts = {key: value for key, value in self.attempts.items() if value[0] > now - 600}
            keys = [("login", login), ("peer", peer)]
            if any(self.attempts.get(key, (now, 0))[1] >= limit for key, limit in zip(keys, (10, 100))):
                return False
            for key in keys:
                start, count = self.attempts.get(key, (now, 0))
                self.attempts[key] = (start, count + 1)
            return True


class SessionAuthMixin:
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def auth_json(self, payload, status=200, cookies=()):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def session_token(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie[SESSION_COOKIE].value if SESSION_COOKIE in cookie else ""
        except CookieError:
            return ""

    def session_cookie(self, token=""):
        secure = bool(os.getenv("RAILWAY_PROJECT_ID") or os.getenv("APP_BASE_URL", "").startswith("https://"))
        value = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_SECONDS if token else 0}"
        return value + ("; Secure" if secure else "")

    def expired_clinic_cookies(self):
        return [f"clinic_access_{clinic}{suffix}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
                for clinic in ("vielle", "inspire", "carla") for suffix in ("", "_team")]

    def same_origin_request(self):
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                if urlsplit(origin).netloc.lower() != self.headers.get("Host", "").lower():
                    return False
            except ValueError:
                return False
        return self.headers.get("X-DOC4DOCS-Request") == "1"

    def require_dashboard_auth(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        public_path = path.removeprefix("/static") if path.startswith("/static/") else path
        if self.command in ("GET", "HEAD") and (public_path in PUBLIC_FILES or path == "/login"):
            return True
        if self.command == "GET" and path in INTEGRATION_CALLBACKS:
            return True
        self.current_user = self.server.auth_store.session_user(self.session_token())
        if not self.current_user:
            if path.startswith("/api/") or self.command not in ("GET", "HEAD"):
                self.auth_json({"ok": False, "code": "session_required", "error": "Entre para continuar."}, 401,
                               [self.session_cookie()])
            else:
                self.send_response(303)
                self.send_header("Location", "/login?next=/master" if is_admin_path(path) else "/login")
                self.send_header("Set-Cookie", self.session_cookie())
                self.send_header("Content-Length", "0")
                self.end_headers()
            return False
        mutation = self.command == "POST" or path in {"/api/sync", "/api/sync-clinica", "/api/sync-traffic"}
        if mutation and not self.same_origin_request():
            self.auth_json({"ok": False, "error": "Origem da solicitação inválida."}, 403)
            return False
        if is_admin_path(path) and not self.require_master_auth():
            return False
        if not self.require_request_clinic(parsed):
            return False
        if not self.require_request_permission(parsed):
            return False
        return True

    def requested_module(self, parsed):
        query = parse_qs(parsed.query, keep_blank_values=True)
        views = query.get("view", [])
        if len(views) > 1 or (views and views[0] not in VIEW_MODULES):
            raise ValueError("Aba inválida.")
        flags = {"include_followup": "patient_followup", "include_quote_followup": "budget_followup", "include_whatsapp_audit": "whatsapp_review"}
        included = {module for key, module in flags.items() if "1" in query.get(key, [])}
        module = VIEW_MODULES[views[0]] if views else next(iter(included), "dashboard")
        if included - {module}:
            raise ValueError("Solicite somente os dados da aba selecionada.")
        clinic = query.get("clinic", ["vielle"])[0]
        if module not in clinic_modules(clinic):
            raise ValueError("Esta aba não está disponível na clínica.")
        return module

    def require_permission(self, clinic, permission):
        if self.server.auth_store.has_permission(self.current_user["id"], clinic, permission):
            return True
        self.auth_json({"ok": False, "code": "permission_denied", "error": "Seu usuário não tem permissão para esta operação."}, 403)
        return False

    def require_request_permission(self, parsed):
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
        clinic = query.get("clinic", ["vielle"])[0]
        if is_admin_path(path) or path.startswith("/api/auth/") or path == "/api/clinic-access":
            return True
        actions = {
            "/api/monthly-goal": "dashboard.edit" if self.command == "POST" else "dashboard.view",
            "/api/patient-followup-contact": "patient_followup.create",
            "/api/patient-followup-status": "patient_followup.edit",
            "/api/quote-followup-contact": "budget_followup.create",
            "/api/quote-followup-status": "budget_followup.edit",
            "/api/whatsapp-audit-review": "whatsapp_review.edit",
            "/api/whatsapp-audit-ai": "whatsapp_review.edit",
            "/api/clinica-patient-link": "patient_followup.edit",
            "/api/sync-traffic": "paid_traffic.edit",
            "/kommo-widget": "commercial.view",
            "/acompanhamento": "patient_followup.view",
        }
        if path in actions:
            return self.require_permission(clinic, actions[path])
        if path in {"/api/report", "/api/export-pdf", "/api/export-authorize"} or "view" in query or "print" in query:
            try:
                module = self.requested_module(parsed)
            except ValueError as error:
                self.auth_json({"ok": False, "error": str(error)}, 400)
                return False
            self.report_module = module
            action = "export" if path in {"/api/export-pdf", "/api/export-authorize"} or query.get("print", [""])[0] == "1" else "view"
            return self.require_permission(clinic, f"{module}.{action}")
        if path.startswith("/api/"):
            self.auth_json({"ok": False, "error": "Rota não encontrada."}, 404)
            return False
        return True

    def require_user_clinic(self, clinic_key):
        if not isinstance(clinic_key, str) or clinic_key not in SUPPORTED_CLINICS:
            self.auth_json({"ok": False, "code": "invalid_clinic", "error": "Clínica inválida."}, 400)
            return False
        if not self.server.auth_store.user_can_access_clinic(self.current_user["id"], clinic_key):
            self.auth_json({"ok": False, "code": "clinic_forbidden", "error": "Você não possui acesso a esta clínica."}, 403)
            return False
        return True

    def require_request_clinic(self, parsed):
        # Check before any clinic context/database is opened. Body-selected access
        # codes are also checked by their handler; integration callbacks keep their
        # independent signature/state validation.
        params = parse_qs(parsed.query, keep_blank_values=True)
        keys = params.get("clinic")
        if keys is not None:
            if len(keys) != 1:
                self.auth_json({"ok": False, "code": "invalid_clinic", "error": "Informe apenas uma clínica."}, 400)
                return False
            return self.require_user_clinic(keys[0])
        path = parsed.path
        if path == "/acompanhamento":
            return self.require_user_clinic("vielle")
        scoped_api = path.startswith("/api/") and not path.startswith(("/api/auth/", "/api/master/")) and path not in {"/api/clinic-access", "/api/master"}
        if scoped_api or path in {"/kommo-widget", "/auth/start"}:
            return self.require_user_clinic("vielle")
        return True

    def require_master_auth(self):
        user = self.server.auth_store.session_user(self.session_token())
        if user and user["is_master"]:
            self.current_user = user
            return True
        self.auth_json({"ok": False, "code": "master_required", "error": "Acesso exclusivo do Master."}, 403)
        return False

    def send_head(self):
        # Resolve the actual file too, covering encoded and /static/ path aliases.
        target = Path(self.translate_path(self.path)).resolve()
        protected = {Path(self.directory).resolve() / name for name in ("master.html", "settings.html", "settings.js")}
        if target in protected and not self.require_master_auth():
            return None
        return super().send_head()

    def handle_session_post(self, path):
        if path not in ("/api/auth/login", "/api/auth/logout"):
            return False
        if not self.same_origin_request():
            self.auth_json({"ok": False, "error": "Origem da solicitação inválida."}, 403)
            return True
        if path.endswith("/logout"):
            self.server.auth_store.logout(self.session_token())
            self.auth_json({"ok": True}, cookies=[self.session_cookie(), *self.expired_clinic_cookies()])
            return True
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get_content_type() != "application/json":
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            login, password = data.get("login"), data.get("password")
            if not isinstance(login, str) or not isinstance(password, str):
                raise ValueError()
        except (ValueError, UnicodeError):
            self.auth_json({"ok": False, "error": "Informe login e senha válidos."}, 400)
            return True
        if not self.server.login_limiter.allow(login, self.client_address[0]):
            self.server.auth_store.audit_event("login_denied", details={"reason": "rate_limit"})
            self.auth_json({"ok": False, "error": "Muitas tentativas. Tente novamente em 10 minutos."}, 429)
            return True
        token = self.server.auth_store.login(login, password, self.session_token())
        if not token:
            self.auth_json({"ok": False, "error": "Login ou senha inválidos, ou acesso inativo."}, 401)
        else:
            self.auth_json({"ok": True}, cookies=[self.session_cookie(token), *self.expired_clinic_cookies()])
        return True

    def do_HEAD(self):
        if not self.require_dashboard_auth():
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/login":
            self.path = "/login.html"
        elif parsed.path in ("/master", "/master/"):
            self.path = "/master.html"
        elif parsed.path.startswith("/static/"):
            self.path = "/" + parsed.path.removeprefix("/static/")
        super().do_HEAD()
