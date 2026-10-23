import contextlib
import http.client
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from auth_store import AuthStore
from auth_http import LoginLimiter
from clinic_catalog import SUPPORTED_CLINICS
from access_policy import clinic_permissions


class HttpAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {"DATA_DIR": cls.temp.name, "VIELLE_ACCESS_CODE": "clinic-test-code"})
        cls.env.start()
        cls.app = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.dbtemp = tempfile.TemporaryDirectory()
        self.addCleanup(self.dbtemp.cleanup)
        self.store = AuthStore(Path(self.dbtemp.name) / "auth.sqlite3")
        self.store.initialize()
        self.user_id = self.store.create_user("Usuario Local", "teste", "Temporary-test-password!", clinic_keys=["vielle"],
            permissions={"vielle": [p for p in clinic_permissions("vielle") if p != "crm.view"]})
        # Exercise the actual Handler, while preventing clinic migrations and external calls.
        def start_patch(patcher):
            result = patcher.start()
            self.addCleanup(patcher.stop)
            return result
        start_patch(patch.object(self.app, "clinic_context", side_effect=lambda _: contextlib.nullcontext()))
        start_patch(patch.object(self.app, "config_value", side_effect=lambda key, default=None: "test-secret" if key == "APP_SECRET" else default))
        self.report = start_patch(patch.object(self.app, "report_data", return_value={"ok": True, "test": "clinic-report"}))
        start_patch(patch.object(self.app, "query_report_args", return_value={}))
        start_patch(patch("urllib.request.urlopen", side_effect=AssertionError("External network is forbidden in tests")))
        start_patch(patch.object(self.app.Handler, "log_message", return_value=None))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        self.server.auth_store = self.store
        self.server.login_limiter = LoginLimiter()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, cookie="", headers=None):
        headers = dict(headers or {})
        if cookie:
            headers["Cookie"] = cookie
        if isinstance(body, dict):
            body = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        result = response.status, response.getheaders(), response.read()
        conn.close()
        return result

    def login(self):
        status, headers, body = self.request("POST", "/api/auth/login",
            {"login": "teste", "password": "Temporary-test-password!"}, headers={"X-DOC4DOCS-Request": "1"})
        self.assertEqual(status, 200, body)
        cookie = next(value.split(";", 1)[0] for key, value in headers if key == "Set-Cookie" and value.startswith("doc4docs_session="))
        return cookie, headers

    def test_anonymous_pages_and_endpoints_are_blocked(self):
        for path in ("/?clinic=vielle", "/index.html", "/static/index.html", "/settings.html", "/acompanhamento", "/kommo-widget", "/static/../index.html"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 303, path)
            self.assertTrue(dict(headers)["Location"].startswith("/login"))
            self.assertNotIn(b"RESUMO MENSAL", body)
        for path in ("/api/report?clinic=vielle", "/api/settings", "/api/sync", "/api/sync-clinica", "/api/sync-traffic", "/api/monthly-goal", "/api/auth/me"):
            self.assertEqual(self.request("GET", path)[0], 401, path)
        for path in ("/api/clinic-access", "/api/patient-followup-contact", "/api/quote-followup-status", "/api/whatsapp-audit-ai", "/api/clear-data"):
            self.assertEqual(self.request("POST", path, {})[0], 401, path)
        self.assertEqual(self.request("HEAD", "/index.html")[0], 303)
        self.report.assert_not_called()

    def test_marketing_home_preserves_anonymous_and_authenticated_boundaries(self):
        for path in ("/", "/?utm_source=campanha", "/inicio", "/inicio/", "/home.html", "/static/home.html", "/planos", "/planos/", "/plans.html", "/marketing.css", "/marketing.js"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 200)
                self.assertEqual(self.request("HEAD", path)[0], 200)
        _, _, body = self.request("GET", "/")
        self.assertIn(b"Sua cl", body)
        self.assertNotIn(b"id=\"viewTabs\"", body)
        for path in ("/marketing-hero-panorama-v2.png", "/marketing-palm-general-v2.png", "/marketing-hero-owner-v3.png", "/marketing-monthly-v3.png", "/marketing-sales-v3.png", "/marketing-procedures-v3.png"):
            status, headers, raw = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertEqual({key.lower(): value for key, value in headers}["content-type"], "image/png")
            self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(self.request("HEAD", path)[0], 200)
        for path in ("/?clinic=vielle", "/?view=financialView", "/inicio/../index.html", "/static/home.html/../index.html", "/master/commercial.html"):
            self.assertEqual(self.request("GET", path)[0], 303, path)
        cookie, _ = self.login()
        _, _, body = self.request("GET", "/", cookie=cookie)
        self.assertIn(b"id=\"viewTabs\"", body)
        self.report.assert_not_called()

    def test_website_requests_are_stored_privately_and_only_master_can_manage(self):
        payload = {"request_id": "a" * 32, "name": "Visitante Exemplo", "organization": "Clínica Exemplo",
                   "email": "visitante@example.test", "phone": "+5511999990000", "plan": "Conectado", "privacy": True,
                   "attribution": {"utm_source": "google", "utm_campaign": "demonstracao"}, "source_path": "/planos"}
        headers = {"X-DOC4DOCS-Request": "1"}
        status, _, raw = self.request("POST", "/api/public/demo", payload, headers=headers)
        self.assertEqual(status, 201, raw)
        self.assertEqual(json.loads(raw), {"ok": True})
        self.assertEqual(self.request("POST", "/api/public/demo", payload, headers=headers)[0], 201)
        self.assertEqual(self.request("GET", "/api/public/demo")[0], 405)
        self.assertEqual(self.request("GET", "/api/master/marketing-leads")[0], 401)
        cookie, _ = self.login()
        self.assertEqual(self.request("GET", "/api/master/marketing-leads", cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/master/commercial.html", cookie=cookie)[0], 403)
        master_id = self.store.create_user("Master Comercial", "master-sales", "Temporary-test-password!", is_master=True)
        token = self.store.login("master-sales", "Temporary-test-password!")
        master_cookie = "doc4docs_session=" + token
        status, _, raw = self.request("GET", "/api/master/marketing-leads", cookie=master_cookie)
        self.assertEqual(status, 200, raw)
        data = json.loads(raw)
        self.assertEqual(data["total"], 1)
        lead = data["leads"][0]
        self.assertNotIn("request_hash", lead)
        self.assertEqual(lead["attribution"]["utm_campaign"], "demonstracao")
        self.assertEqual(self.request("GET", "/master/commercial.html", cookie=master_cookie)[0], 200)
        update = {"revision": lead["revision"], "status": "contacted", "notes": "Retorno combinado."}
        self.assertEqual(self.request("POST", "/api/master/marketing-leads/" + lead["id"], update, cookie=master_cookie, headers=headers)[0], 200)
        self.assertEqual(self.request("POST", "/api/master/marketing-leads/" + lead["id"], update, cookie=master_cookie, headers=headers)[0], 400)
        with self.store.connection() as c:
            event = c.execute("SELECT actor_id FROM audit_log WHERE action='marketing_lead_updated'").fetchone()
            self.assertEqual(event[0], master_id)
        self.report.assert_not_called()

    def test_website_submission_rejects_cross_origin_and_private_fields(self):
        payload = {"request_id": "b" * 32, "name": "Exemplo", "organization": "Exemplo", "email": "e@example.test",
                   "phone": "+5511999990000", "plan": "Essencial", "privacy": True}
        self.assertEqual(self.request("POST", "/api/public/demo", payload)[0], 403)
        headers = {"X-DOC4DOCS-Request": "1", "Origin": "https://evil.example"}
        self.assertEqual(self.request("POST", "/api/public/demo", payload, headers=headers)[0], 403)
        for extra in ({"status": "won"}, {"notes": "override"}, {"doctor_id": "private"}, {"website": "robot"}, {"privacy": False}, {"phone": "+38997357320"}, {"email": "invalid"}):
            status, _, raw = self.request("POST", "/api/public/demo", {**payload, **extra}, headers={"X-DOC4DOCS-Request": "1"})
            self.assertEqual(status, 400, raw)

    def test_minimal_website_request_and_contact_limit_use_phone(self):
        payload = {"request_id": "c" * 32, "name": "Gestor demonstrativo", "phone": "+5511987654321",
                   "plan": "Demonstração", "privacy": True, "attribution": {"utm_source": "teste"}}
        headers = {"X-DOC4DOCS-Request": "1"}
        for i in range(8):
            status, _, raw = self.request("POST", "/api/public/demo", {**payload, "email": "" if i == 0 else f"test-{i}@example.test"}, headers=headers)
            self.assertEqual(status, 201 if i == 0 else 400, raw)
        self.assertEqual(self.request("POST", "/api/public/demo", payload, headers=headers)[0], 429)
        with self.store.connection() as c:
            row = c.execute("SELECT phone, organization FROM marketing_leads WHERE request_id=?", (payload["request_id"],)).fetchone()
            self.assertEqual(tuple(row), ("+5511987654321", ""))

    def test_public_login_and_assets(self):
        for path in ("/login", "/login.html", "/static/login.html", "/login.css", "/login.js", "/doc4docs-logo-white.png"):
            self.assertEqual(self.request("GET", path)[0], 200, path)
        for path in ("/doc4docs-favicon.png?v=20260908", "/static/doc4docs-favicon.png?v=20260908"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertEqual({key.lower(): value for key, value in headers}["content-type"], "image/png")
            self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_pricing_page_and_assets_are_public_without_clinic_access(self):
        paths = ("/precificacao", "/precificacao/", "/precificacao?clinic=inspire",
                 "/pricing.html", "/static/pricing.html", "/pricing.css", "/pricing.js",
                 "/pricing-math.js", "/body-icons.js", "/pricing-pdf-lib.min.js",
                 "/design-system.css?v=1", "/static/design-system.css?v=1",
                 "/app-shell.js?v=1", "/static/app-shell.js?v=1")
        with patch.object(self.app, "db", side_effect=AssertionError("No clinic DB access")):
            for path in paths:
                self.assertEqual(self.request("GET", path)[0], 200, path)
                self.assertEqual(self.request("HEAD", path)[0], 200, path)
        self.report.assert_not_called()

    def test_privacy_and_app_identity_pages_are_public_without_private_data(self):
        with patch.object(self.app,'db',side_effect=AssertionError('No clinic DB access')):
            for path in ('/privacidade.html','/sobre.html','/legal.css'):
                self.assertEqual(self.request('GET',path)[0],200,path)
                self.assertEqual(self.request('HEAD',path)[0],200,path)
        self.report.assert_not_called()

    def test_public_pricing_does_not_bypass_private_routes(self):
        for path in ("/pricing/../index.html", "/precificacao/../master", "/static/../settings.html"):
            self.assertEqual(self.request("GET", path)[0], 303, path)
        for path in ("/api/report?clinic=inspire", "/api/master/users", "/api/settings"):
            self.assertEqual(self.request("GET", path)[0], 401, path)
        self.assertEqual(self.request("POST", "/precificacao", {})[0], 401)
        self.report.assert_not_called()

    def test_login_cookie_and_existing_clinic_gate(self):
        cookie, headers = self.login()
        session_header = next(value for key, value in headers if key == "Set-Cookie" and value.startswith("doc4docs_session="))
        self.assertIn("HttpOnly", session_header)
        self.assertIn("SameSite=Lax", session_header)
        self.assertIn("no-store", dict(headers)["Cache-Control"])
        self.assertEqual(self.request("GET", "/", cookie=cookie)[0], 200)
        status, _, body = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn(b"password_hash", body)
        self.assertEqual(self.request("GET", "/api/report?clinic=vielle", cookie=cookie)[0], 200)
        status, headers, _ = self.request("POST", "/api/clinic-access", {"clinic_id": "vielle", "access_code": "clinic-test-code"},
                                        cookie=cookie, headers={"X-DOC4DOCS-Request": "1"})
        self.assertEqual(status, 200)
        self.assertFalse(any(key == "Set-Cookie" for key, value in headers))
        clinic_cookie = "clinic_access_vielle=obsolete"
        self.assertEqual(self.request("GET", "/api/report?clinic=vielle", cookie=cookie + "; " + clinic_cookie)[0], 200)

    def test_logout_disable_and_password_reset_block_existing_cookie(self):
        cookie, _ = self.login()
        self.assertEqual(self.request("POST", "/api/auth/logout", cookie=cookie, headers={"X-DOC4DOCS-Request": "1"})[0], 200)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=cookie)[0], 401)
        cookie, _ = self.login()
        self.store.set_user_active(self.user_id, False)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=cookie)[0], 401)
        self.store.set_user_active(self.user_id, True)
        cookie, _ = self.login()
        self.store.set_password(self.user_id, "Replaced-test-password!")
        self.assertEqual(self.request("GET", "/index.html", cookie=cookie)[0], 303)
        self.assertEqual(self.request("GET", "/", cookie=cookie)[0], 200)

    def test_csrf_and_bad_login(self):
        data = {"login": "teste", "password": "Temporary-test-password!"}
        self.assertEqual(self.request("POST", "/api/auth/login", data)[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/login", data, headers={"X-DOC4DOCS-Request": "1", "Origin": "https://other.test"})[0], 403)
        data["password"] = "wrong"
        self.assertEqual(self.request("POST", "/api/auth/login", data, headers={"X-DOC4DOCS-Request": "1"})[0], 401)
        cookie, _ = self.login()
        self.assertEqual(self.request("GET", "/api/sync", cookie=cookie)[0], 403)

    def test_integration_callbacks_keep_their_own_validation(self):
        self.assertEqual(self.request("POST", "/api/clinica-webhook", {})[0], 401)
        with patch.object(self.app, "sign_revoke_query", return_value=False), patch.object(self.app, "db") as db:
            self.assertEqual(self.request("GET", "/webhooks/revoked")[0], 401)
            db.assert_not_called()
        with patch.object(self.app, "consume_state", return_value=False):
            status, headers, _ = self.request("GET", "/auth/callback?state=invalid")
            self.assertEqual(status, 302)
            self.assertIn("estado_de_autorizacao_invalido", dict(headers)["Location"])

    def test_secure_cookie_in_railway(self):
        with patch.dict(os.environ, {"RAILWAY_PROJECT_ID": "test"}):
            _, headers = self.login()
        cookie = next(value for key, value in headers if key == "Set-Cookie" and value.startswith("doc4docs_session="))
        self.assertIn("; Secure", cookie)

    def test_common_user_cannot_open_admin_files_or_spoof_old_master_headers(self):
        cookie, _ = self.login()
        headers = {"X-DOC4DOCS-Request": "1", "X-Master-User": "master", "X-Master-Password": "old-password"}
        for path in ("/master", "/master/", "/master.html", "/static/master.html", "/static/../master.html",
                     "/%6daster.html", "/static/%2e%2e/settings.html", "/settings.html", "/settings.js", "/api/settings"):
            self.assertEqual(self.request("GET", path, cookie=cookie, headers=headers)[0], 403, path)
            self.assertEqual(self.request("HEAD", path, cookie=cookie, headers=headers)[0], 403, path)
        with patch.object(self.app, "sync_all") as sync, patch.object(self.app, "clear_local_data") as clear:
            for path in ("/api/settings", "/api/sync-all", "/api/clear-data", "/api/reset-kommo", "/api/master/users"):
                self.assertEqual(self.request("POST", path, {"confirm": "LIMPAR"}, cookie, headers)[0], 403, path)
            sync.assert_not_called()
            clear.assert_not_called()

    def test_master_uses_session_for_page_and_settings(self):
        master_id = self.store.create_first_master("Administrador", "master", "Master-test-password!")
        token = self.store.login("master", "Master-test-password!")
        cookie = "doc4docs_session=" + token
        for path in ("/master", "/master/", "/master.html", "/static/master.html", "/settings.html"):
            self.assertEqual(self.request("GET", path, cookie=cookie)[0], 200, path)
        with patch.object(self.app, "settings_payload", return_value={"ok": True, "config": {}}):
            self.assertEqual(self.request("GET", "/api/settings", cookie=cookie)[0], 200)
        with self.store.connection() as conn:
            conn.execute("UPDATE users SET is_master = 0 WHERE id = ?", (master_id,))
        self.assertEqual(self.request("GET", "/master", cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/api/settings", cookie=cookie)[0], 403)

    def test_anonymous_master_route_requires_login_even_with_legacy_headers(self):
        status, headers, _ = self.request("GET", "/master", headers={"X-Master-User": "master"})
        self.assertEqual(status, 303)
        self.assertEqual(dict(headers)["Location"], "/login?next=/master")
        self.assertEqual(self.request("GET", "/api/settings", headers={"X-Master-User": "master"})[0], 401)

    def test_meta_token_reuse_is_master_only_and_checks_origin(self):
        cookie, _ = self.login()
        payload = {"values": {}, "meta_token_institute": "victor-paranhos"}
        with patch.object(self.app, "save_config_values") as save:
            self.assertEqual(self.request("POST", "/api/settings?clinic=vielle", payload, cookie,
                {"X-DOC4DOCS-Request": "1"})[0], 403)
            self.store.create_first_master("Master", "master", "Master-test-password!")
            master = "doc4docs_session=" + self.store.login("master", "Master-test-password!")
            self.assertEqual(self.request("POST", "/api/settings?clinic=vielle", payload, master)[0], 403)
            save.assert_not_called()

    def test_crm_pilot_authentication_clinic_and_origin_boundaries(self):
        self.assertEqual(self.request("GET", "/api/crm?clinic=vielle")[0], 401)
        self.assertEqual(self.request("GET", "/api/crm/appointments?clinic=vielle&id=other")[0], 401)
        cookie, _ = self.login()
        self.assertEqual(self.request("GET", "/api/crm?clinic=vielle", cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/api/crm/commercial?clinic=vielle&id=other&scope=clinic", cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/api/crm/appointments?clinic=vielle&id=other&scope=clinic", cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/static/crm.html?clinic=vielle", cookie=cookie)[0], 403)
        master = self.master_cookie()
        self.assertEqual(self.request("GET", "/api/crm?clinic=inspire", cookie=master)[0], 403)
        self.assertEqual(self.request("GET", "/api/crm", cookie=master)[0], 403)
        self.assertEqual(self.request("GET", "/api/crm?clinic=vielle&clinic=inspire", cookie=master)[0], 400)
        self.assertEqual(self.request("POST", "/api/crm/doctor?clinic=vielle", {"name":"A"}, cookie=master)[0], 403)

    def test_crm_pilot_master_can_prepare_structure_without_external_calls(self):
        master = self.master_cookie()
        headers = {"X-DOC4DOCS-Request":"1"}
        status, _, raw = self.request("POST", "/api/crm/bootstrap?clinic=vielle", {}, master, headers)
        self.assertEqual(status, 200, raw)
        status, _, raw = self.request("GET", "/api/crm?clinic=vielle", cookie=master)
        self.assertEqual(status, 200, raw)
        data = json.loads(raw)
        self.assertEqual(len(data["doctors"]), 5)
        self.assertFalse(data["sends_enabled"])
        self.assertEqual(data["leads"], [])

    def test_crm_webhook_has_separate_authentication(self):
        with patch.dict(os.environ, {"CRM_WEBHOOK_SECRET":"x"*40}):
            self.assertEqual(self.request("POST", "/api/crm-webhook", {})[0], 401)
            self.assertEqual(self.request("POST", "/api/crm-webhook", {}, headers={"X-DOC4DOCS-Webhook":"wrong"})[0], 401)
            self.assertEqual(self.request("POST", "/api/crm-webhook", {}, headers={"X-DOC4DOCS-Webhook":"x"*40})[0], 400)

    def test_crm_master_can_create_and_edit_more_than_twelve_pipeline_stages(self):
        from crm_api import store_for
        store = store_for(self.store)
        store.bootstrap({"Doutor teste": ["Teste"]})
        doctor = store.listing()["doctor_id"]
        cookie = self.master_cookie()
        headers = {"X-DOC4DOCS-Request": "1"}
        url = f"/api/crm/pipeline?clinic=vielle&doctor={doctor}"
        payload = {"doctor_id": doctor, "name": "Funil grande", "stages": [
            {"name": f"Fase {i + 1}", "kind": "open"} for i in range(20)]}
        status, _, raw = self.request("POST", url, payload, cookie, headers)
        self.assertEqual(status, 200, raw)
        key = json.loads(raw)["id"]
        pipeline = next(p for p in store.listing(doctor)["pipelines"] if p["id"] == key)
        self.assertEqual(len(pipeline["stages"]), 20)
        status, _, raw = self.request("POST", url, {**pipeline, "name": "Funil renomeado"}, cookie, headers)
        self.assertEqual(status, 200, raw)
        saved = next(p for p in store.listing(doctor)["pipelines"] if p["id"] == key)
        self.assertEqual(saved["name"], "Funil renomeado")
        self.assertEqual(saved["stages"], pipeline["stages"])
        self.assertEqual(self.request("POST", url, pipeline, cookie, headers)[0], 409)

    def test_crm_dashboard_permission_and_operator_visibility(self):
        from crm_api import store_for
        store = store_for(self.store)
        store.bootstrap({"Doutor teste": ["Teste"]})
        doctor = store.listing()["doctor_id"]
        path = f"/api/crm/dashboard?clinic=vielle&doctor={doctor}"
        cookie, _ = self.login()
        self.assertEqual(self.request("GET", path)[0], 401)
        self.assertEqual(self.request("GET", path, cookie=cookie)[0], 403)
        self.limit_permissions(["crm.view"])
        status, _, raw = self.request("GET", path, cookie=cookie)
        self.assertEqual(status, 200, raw)
        self.assertIsNone(json.loads(raw)["operators"])
        master = self.master_cookie()
        status, _, raw = self.request("GET", path, cookie=master)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["operators"], [])
        self.assertEqual(self.request("GET", path + "&days=365", cookie=master)[0], 400)
        self.assertEqual(self.request("GET", path.replace("clinic=vielle", "clinic=inspire"), cookie=master)[0], 403)

    def test_crm_staff_permission_allows_work_without_master_configuration(self):
        from crm_api import store_for, MASTER_ACTIONS
        store = store_for(self.store)
        store.bootstrap({"Doutor teste": ["Teste"]})
        self.limit_permissions(["crm.view"])
        cookie, _ = self.login()
        headers = {"X-DOC4DOCS-Request": "1"}
        status, _, raw = self.request("GET", "/api/crm?clinic=vielle", cookie=cookie)
        self.assertEqual(status, 200, raw)
        data = json.loads(raw)
        self.assertFalse(data["can_manage"])
        self.assertFalse(data["can_enable_campaigns"])
        self.assertEqual(self.request("GET", "/crm.html?clinic=vielle", cookie=cookie)[0], 200)
        self.assertEqual(self.request("GET", "/static/crm.html?clinic=vielle", cookie=cookie)[0], 200)
        for action in MASTER_ACTIONS:
            status, _, _ = self.request("POST", "/api/crm/" + action + "?clinic=vielle", {}, cookie, headers)
            self.assertEqual(status, 403, action)
        doctor, pipeline = data["doctor_id"], data["pipelines"][0]
        status, _, raw = self.request("POST", "/api/crm/lead?clinic=vielle", {"doctor_id": doctor,
            "pipeline_id": pipeline["id"], "stage_id": pipeline["stages"][0]["id"], "name": "Paciente sintetico",
            "phone": "5500000000002", "title": "Retorno"}, cookie, headers)
        self.assertEqual(status, 200, raw)
        lead_id = json.loads(raw)["id"]
        contact = store.detail(lead_id, doctor)["lead"]["contact_id"]
        channel = store.channel({"doctor_id": doctor, "name": "Atendimento", "phone": "5500000000001"})
        with store.db() as c:
            c.execute("UPDATE channels SET state='open' WHERE id=?", (channel,))
        with patch("crm_api.sends_enabled", return_value=True), patch.object(store, "worker_online", return_value=True):
            status, _, raw = self.request("POST", "/api/crm/reply?clinic=vielle", {"doctor_id": doctor,
                "channel_id": channel, "contact_id": contact, "body": "Resposta de teste", "request_id": "staff-reply"}, cookie, headers)
        self.assertEqual(status, 200, raw)
        message = store.conversation(doctor, channel, contact)["messages"][-1]
        self.assertEqual(message["body"], "*Usuario Local*\n\nResposta de teste")
        self.assertEqual(self.request("GET", "/api/crm?clinic=inspire", cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/master", cookie=cookie)[0], 403)
        self.limit_permissions([])
        self.assertEqual(self.request("GET", "/api/crm?clinic=vielle", cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/crm.html?clinic=vielle", cookie=cookie)[0], 403)
        self.assertEqual(self.request("POST", "/api/crm/lead?clinic=vielle", {}, cookie, headers)[0], 403)

    def test_crm_media_raw_upload_and_private_download_http(self):
        from crm_api import store_for
        from urllib.parse import urlencode
        store = store_for(self.store)
        store.bootstrap({"Doutor teste": ["Teste"]})
        doctor = store.listing()["doctor_id"]
        channel = store.channel({"doctor_id": doctor, "name": "Atendimento", "phone": "5500000000001"})
        with store.db() as c:
            instance = store.row(c, "channels", channel)["instance"]
        store.receive(instance, {"key": {"id": "media-in", "remoteJid": "5500000000002@s.whatsapp.net"},
            "message": {"conversation": "Ola"}}, "messages.upsert")
        contact = store.inbox(doctor, "test")["conversations"][0]["contact_id"]
        path = "/api/crm/media-upload?" + urlencode({"clinic": "vielle", "doctor": doctor, "channel": channel, "contact": contact, "kind": "document"})
        master = self.master_cookie()
        raw = b"Arquivo sintetico"
        headers = {"X-DOC4DOCS-Request": "1", "X-File-Name": "arquivo.txt", "X-Upload-Id": "raw-http", "Content-Type": "text/plain"}
        self.assertEqual(self.request("POST", path, raw, master)[0], 403)
        status, _, body = self.request("POST", path, raw, master, headers)
        self.assertEqual(status, 200, body)
        media = json.loads(body)["media"]
        self.assertEqual(self.request("GET", media["url"])[0], 401)
        user, _ = self.login()
        self.assertEqual(self.request("GET", media["url"], cookie=user)[0], 403)
        status, result_headers, body = self.request("GET", media["url"], cookie=master)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, raw)
        self.assertIn("no-store", dict(result_headers)["Cache-Control"])
        self.assertTrue(dict(result_headers)["Content-Disposition"].startswith("attachment;"))

    def test_crm_patient_picker_and_refresh_keep_master_origin_and_doctor_boundaries(self):
        from crm_api import store_for
        store = store_for(self.store)
        store.bootstrap({"Doutor teste": ["Teste"]})
        doctor = store.listing()["doctor_id"]
        pipeline = store.listing()["pipelines"][0]
        lead = store.lead({"doctor_id": doctor, "pipeline_id": pipeline["id"], "stage_id": pipeline["stages"][0]["id"],
                           "name": "Contato", "phone": "5500000000002", "title": "Retorno"}, "Equipe")
        master, user = self.master_cookie(), self.login()[0]
        headers = {"X-DOC4DOCS-Request": "1"}
        with patch("crm_patient_refresh.start_refresh", return_value={"ok": True, "started": True, "running": True}) as start:
            for action in ("patients", "patient-directory-refresh", "patient-refresh"):
                path = f"/api/crm/{action}?clinic=vielle&doctor={doctor}&id={lead}"
                self.assertEqual(self.request("GET", path)[0], 401)
                self.assertEqual(self.request("GET", path, cookie=user)[0], 403)
                self.assertEqual(self.request("GET", path.replace("clinic=vielle", "clinic=inspire"), cookie=master)[0], 403)
            for action, payload in (("patient-directory-refresh", {"doctor_id": "wrong"}),
                                    ("patient-refresh", {"doctor_id": "wrong", "id": lead})):
                path = f"/api/crm/{action}?clinic=vielle"
                self.assertEqual(self.request("POST", path, payload, master)[0], 403)
                self.assertIn(self.request("POST", path, payload, master, headers)[0], (400, 404))
            start.assert_not_called()
            self.assertEqual(self.request("POST", "/api/crm/patient-directory-refresh?clinic=vielle", {"doctor_id": doctor}, master, headers)[0], 200)
            self.assertEqual(self.request("POST", "/api/crm/patient-refresh?clinic=vielle", {"doctor_id": doctor, "id": lead}, master, headers)[0], 200)
            self.assertEqual(start.call_count, 2)
        with patch.object(store.patient_directory, "search", return_value={"items": [], "total": 0}) as search:
            self.assertEqual(self.request("GET", f"/api/crm/patients?clinic=vielle&doctor={doctor}&search=Teste&page=2", cookie=master)[0], 200)
            search.assert_called_once_with("Teste", "2")

    def test_crm_inbox_close_and_reply_http_boundaries(self):
        from crm_api import store_for
        store=store_for(self.store)
        store.bootstrap({"Doutor teste":["Teste"]})
        doctor=store.listing()["doctor_id"]
        channel=store.channel({"doctor_id":doctor,"name":"Atendimento","phone":"5500000000001"})
        with store.db() as c:instance=store.row(c,"channels",channel)["instance"]
        store.receive(instance,{"key":{"id":"in-test","remoteJid":"5500000000002@s.whatsapp.net"},"message":{"conversation":"Ola"}},"messages.upsert")
        master=self.master_cookie()
        inbox=f"/api/crm/inbox?clinic=vielle&doctor={doctor}"
        user,_=self.login()
        self.assertEqual(self.request("GET",inbox,cookie=user)[0],403)
        status,_,raw=self.request("GET",inbox,cookie=master)
        self.assertEqual(status,200,raw)
        item=json.loads(raw)["conversations"][0]
        path=f"/api/crm/conversation?clinic=vielle&doctor={doctor}&channel={channel}&contact={item['contact_id']}"
        status,_,raw=self.request("GET",path,cookie=master)
        self.assertEqual(status,200,raw)
        payload={"doctor_id":doctor,"channel_id":channel,"contact_id":item["contact_id"],"cursor":json.loads(raw)["inbound_cursor"]}
        self.assertEqual(json.loads(self.request("GET",inbox,cookie=master)[2])["counts"]["unread"],1)
        self.assertEqual(self.request("POST","/api/crm/conversation-read?clinic=vielle",payload,master)[0],403)
        self.assertEqual(self.request("POST","/api/crm/conversation-read?clinic=vielle",payload,master,{"X-DOC4DOCS-Request":"1"})[0],404)
        self.assertEqual(self.request("POST","/api/crm/conversation-close?clinic=vielle",payload,user,{"X-DOC4DOCS-Request":"1"})[0],403)
        self.assertEqual(self.request("POST","/api/crm/conversation-close?clinic=vielle",payload,master)[0],403)
        self.assertEqual(self.request("POST","/api/crm/conversation-close?clinic=vielle",{**payload,"doctor_id":"wrong"},master,{"X-DOC4DOCS-Request":"1"})[0],400)
        self.assertEqual(self.request("POST","/api/crm/conversation-close?clinic=vielle",payload,master,{"X-DOC4DOCS-Request":"1"})[0],200)
        self.assertEqual(json.loads(self.request("GET",inbox,cookie=master)[2])["counts"]["unread"],0)
        store.receive(instance,{"key":{"id":"in-reopened","remoteJid":"5500000000002@s.whatsapp.net"},"message":{"conversation":"Mais uma duvida"}},"messages.upsert")
        payload["cursor"]=json.loads(self.request("GET",path,cookie=master)[2])["inbound_cursor"]
        self.assertEqual(self.request("POST","/api/crm/reply?clinic=vielle",{**payload,"body":"Oi","request_id":"http-1"},master,{"X-DOC4DOCS-Request":"1"})[0],400)
        with store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(channel,))
        with patch("crm_api.sends_enabled",return_value=True),patch.object(store,"worker_online",return_value=True):
            status,_,raw=self.request("POST","/api/crm/reply?clinic=vielle",{**payload,"body":"Oi","request_id":"http-1","sender_name":"Nome falso"},master,{"X-DOC4DOCS-Request":"1"})
        self.assertEqual(status,200,raw)
        message=json.loads(self.request("GET",path,cookie=master)[2])["messages"][-1]
        self.assertEqual(message["body"],"*Master*\n\nOi")
        self.assertEqual(message["sender_name"],"Master")
        self.assertNotEqual(message["sender_user_id"],"")
        self.assertEqual(json.loads(self.request("GET",inbox,cookie=master)[2])["counts"]["unread"],1)
        store.finish(store.claim(),"http-accepted")
        self.assertEqual(json.loads(self.request("GET",inbox,cookie=master)[2])["counts"]["unread"],0)

    def test_manual_pdf_reply_is_not_blocked_by_another_unconfirmed_contact(self):
        from crm_api import store_for
        from crm_media import upload
        from crm_worker import tick
        from unittest.mock import Mock
        store = store_for(self.store)
        store.bootstrap({"Doutor teste": ["Teste"]})
        doctor = store.listing()["doctor_id"]
        channel = store.channel({"doctor_id": doctor, "name": "Organico", "phone": "5500000000001"})
        with store.db() as c:
            instance = store.row(c, "channels", channel)["instance"]
            c.execute("UPDATE channels SET state='open' WHERE id=?", (channel,))
        for index in (2, 3):
            store.receive(instance, {"key": {"id": f"synthetic-in-{index}", "remoteJid": f"550000000000{index}@s.whatsapp.net"},
                "message": {"conversation": "Contato sintetico"}}, "messages.upsert")
        contacts = {r["phone"]: r["contact_id"] for r in store.inbox(doctor, "user")["conversations"]}
        previous = store.reply({"doctor_id": doctor, "channel_id": channel, "contact_id": contacts["5500000000002"],
            "body": "Envio anterior", "request_id": "old-request"}, self.user_id, True, sender_name="Equipe")
        store.finish(store.claim(), error=True)
        target = contacts["5500000000003"]
        media = upload(store, doctor, channel, target, self.user_id, "pdf-upload", "orcamento-sintetico.pdf", "document", b"%PDF-1.4\n%%EOF\n")
        self.limit_permissions(["crm.view"])
        cookie, _ = self.login()
        payload = {"doctor_id": doctor, "channel_id": channel, "contact_id": target, "media_id": media["id"],
                   "body": "", "request_id": "pdf-reply"}
        path = f"/api/crm/reply?clinic=vielle&doctor={doctor}"
        with patch("crm_api.sends_enabled", return_value=True), patch.object(store, "worker_online", return_value=True):
            status, _, raw = self.request("POST", path, payload, cookie, {"X-DOC4DOCS-Request": "1"})
            self.assertEqual(status, 200, raw)
            self.assertEqual(self.request("POST", path, payload, cookie, {"X-DOC4DOCS-Request": "1"})[0], 200)
        gateway = Mock()
        gateway.send.return_value = "pdf-accepted"
        self.assertTrue(tick(store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["contact_id"], target)
        self.assertEqual(gateway.send.call_args.args[0]["media"]["kind"], "document")
        self.assertFalse(tick(store, gateway))
        gateway.send.assert_called_once()
        with store.db() as c:
            self.assertEqual(store.row(c, "messages", previous)["status"], "unknown")

    def test_contact_phone_edit_remove_require_scope_revision_and_confirmation(self):
        from crm_api import store_for
        store = store_for(self.store)
        store.bootstrap({"Doutor teste": ["Permitido", "Restrito"]})
        doctor = store.listing()["doctor_id"]
        allowed, hidden = store.listing(doctor)["pipelines"]
        lead = store.lead({"doctor_id": doctor, "pipeline_id": allowed["id"], "stage_id": allowed["stages"][0]["id"],
            "phone": "5500000000002", "name": "Sintetico", "title": "Teste"}, "Equipe")
        self.limit_permissions(["crm.view"])
        cookie, _ = self.login()
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope={doctor: [allowed["id"]]})
        path = f"/api/crm/phone-update?clinic=vielle&doctor={doctor}"
        payload = {"id": lead, "doctor_id": doctor, "phone": "+55 11 98765-4321", "phone_revision": 1, "confirm": True}
        headers = {"X-DOC4DOCS-Request": "1"}
        self.assertEqual(self.request("POST", path, payload, cookie)[0], 403)
        self.assertEqual(self.request("POST", path, {**payload, "confirm": False}, cookie, headers)[0], 400)
        status, _, raw = self.request("POST", path, payload, cookie, headers)
        self.assertEqual(status, 200, raw)
        updated = json.loads(raw)["lead"]
        self.assertEqual(updated["phone"], "5511987654321")
        self.assertEqual(self.request("POST", path, payload, cookie, headers)[0], 409)
        status, _, raw = self.request("POST", path, {**payload, "phone_revision": updated["phone_revision"], "remove": True}, cookie, headers)
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)["lead"]["phone_removed"])
        with store.db() as c:
            c.execute("UPDATE leads SET pipeline_id=?,stage_id=? WHERE id=?", (hidden["id"], hidden["stages"][0]["id"], lead))
        self.assertEqual(self.request("POST", path, {**payload, "phone_revision": 3}, cookie, headers)[0], 403)

    def test_crm_campaign_stays_blocked_when_manual_replies_are_enabled(self):
        from crm_api import store_for
        store=store_for(self.store)
        store.bootstrap({"Doctor":["Pipeline"]})
        listing=store.listing();doctor=listing["doctor_id"];pipeline=listing["pipelines"][0]
        channel=store.channel({"doctor_id":doctor,"name":"Test","phone":"5500000000001"})
        lead=store.lead({"doctor_id":doctor,"pipeline_id":pipeline["id"],"stage_id":pipeline["stages"][0]["id"],
            "phone":"5500000000002","name":"Test","title":"Test","consent":True,"consent_source":"Test"},"Master")
        campaign=store.campaign({"doctor_id":doctor,"channel_id":channel,"name":"Test","body":"Test","lead_ids":[lead]},"Master")
        with store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(channel,))
        master=self.master_cookie()
        with patch("crm_api.sends_enabled",return_value=True),patch("crm_api.campaigns_enabled",return_value=False),patch.object(store,"worker_online",return_value=True):
            data=json.loads(self.request("GET",f"/api/crm?clinic=vielle&doctor={doctor}",cookie=master)[2])
            self.assertTrue(data["sends_enabled"]);self.assertFalse(data["campaigns_enabled"])
            status,_,raw=self.request("POST","/api/crm/campaign-status?clinic=vielle",
                {"id":campaign,"doctor_id":doctor,"action":"start"},master,{"X-DOC4DOCS-Request":"1"})
            self.assertEqual(status,400,raw)
        with store.db() as c:self.assertEqual(c.execute("SELECT status FROM campaigns WHERE id=?",(campaign,)).fetchone()[0],"draft")

    def test_crm_customer_tier_settings_require_vielle_master_and_origin(self):
        payload={"silver_cents":700000,"gold_cents":2000000}
        user,_=self.login();master=self.master_cookie()
        path="/api/crm/customer-tiers?clinic=vielle"
        self.assertEqual(self.request("POST",path,payload,master)[0],403)
        self.assertEqual(self.request("POST",path,payload,user,{"X-DOC4DOCS-Request":"1"})[0],403)
        self.assertEqual(self.request("POST","/api/crm/customer-tiers?clinic=inspire",payload,master,{"X-DOC4DOCS-Request":"1"})[0],403)
        self.assertEqual(self.request("POST",path,{"silver_cents":1,"gold_cents":1},master,{"X-DOC4DOCS-Request":"1"})[0],400)
        self.assertEqual(self.request("POST",path,payload,master,{"X-DOC4DOCS-Request":"1"})[0],200)
        status,_,raw=self.request("GET","/api/crm?clinic=vielle",cookie=master)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)["customer_tier_rules"],payload)

    def test_crm_search_filters_validate_and_keep_master_clinic_boundary(self):
        from crm_api import store_for
        from urllib.parse import urlencode
        master=self.master_cookie();user,_=self.login()
        store=store_for(self.store);store.bootstrap({"Doutor teste":["Funil"]})
        doctor=store.listing()["doctor_id"]
        for endpoint in ("/api/crm","/api/crm/inbox"):
            def url(payload,clinic="vielle"):
                return endpoint+"?"+urlencode({"clinic":clinic,"doctor":doctor,"filters":json.dumps(payload)})
            self.assertEqual(self.request("GET",url({"sales":"yes"}),cookie=user)[0],403)
            self.assertEqual(self.request("GET",url({"sales":"yes"},"inspire"),cookie=master)[0],403)
            for filters in ({"sales":"bad"},{"min_sales":True},{"unknown":1},{"min_spent_cents":200,"max_spent_cents":100},
                            {"appointment_from":"2026-02-30"},{"appointment_no_upcoming":"true"},
                            {"appointment_from":"2026-10-20","appointment_to":"2026-10-19"}):
                self.assertEqual(self.request("GET",url(filters),cookie=master)[0],400)
            status,_,raw=self.request("GET",url({"sales":"yes","quotes":"active","min_spent_cents":500000,
                "appointment_status":"completed","appointment_from":"2026-09-01","appointment_to":"2026-09-30",
                "appointment_no_upcoming":True}),cookie=master)
            self.assertEqual(status,200,raw)
            self.assertNotIn("_experts_search",raw.decode())

    def test_crm_inbox_search_discovers_only_permitted_leads_without_messages(self):
        from crm_api import store_for
        from urllib.parse import urlencode
        store = store_for(self.store)
        store.bootstrap({"Doutor teste": ["Permitido", "Restrito"]})
        doctor = store.listing()["doctor_id"]
        allowed, hidden = store.listing(doctor)["pipelines"]
        ids = []
        for i, pipeline in enumerate((allowed, hidden)):
            ids.append(store.lead({"doctor_id": doctor, "pipeline_id": pipeline["id"], "stage_id": pipeline["stages"][0]["id"],
                "phone": f"550000000000{i + 2}", "name": f"Contato Busca {i}", "title": "Contato"}, "Equipe"))
        path = "/api/crm/inbox-multi?" + urlencode({"clinic": "vielle", "doctor": doctor, "doctors": json.dumps([doctor]), "search": "Contato Busca"})
        self.assertEqual(self.request("GET", path)[0], 401)
        self.limit_permissions(["crm.view"])
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope={doctor: [allowed["id"]]})
        cookie, _ = self.login()
        status, _, raw = self.request("GET", path, cookie=cookie)
        self.assertEqual(status, 200, raw)
        data = json.loads(raw)
        self.assertEqual(data["conversations"], [])
        self.assertEqual([r["id"] for r in data["lead_results"]], [ids[0]])
        self.assertFalse(data["lead_results"][0]["has_conversation"])
        self.assertNotIn("_experts_search", raw.decode())
        self.assertEqual(self.request("GET", path.replace("vielle", "inspire"), cookie=cookie)[0], 403)
        with store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM leads").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    def test_meta_token_reuse_copies_only_secret_without_returning_it(self):
        from institute_api import store_for
        self.store.create_first_master("Master", "master", "Master-test-password!")
        cookie = "doc4docs_session=" + self.store.login("master", "Master-test-password!")
        institutes = store_for(self.store)
        institutes.save_settings("victor-paranhos", {"meta_access_token": "private-meta-token", "meta_account_id": "123456"})
        payload = {"values": {}, "meta_token_institute": "victor-paranhos"}
        with patch.object(self.app, "save_config_values") as save, patch.object(self.app, "settings_payload", return_value={"ok": True, "config": {}}):
            status, _, raw = self.request("POST", "/api/settings?clinic=vielle", payload, cookie,
                {"X-DOC4DOCS-Request": "1"})
            self.assertEqual(status, 200, raw)
            save.assert_called_once_with({"META_ACCESS_TOKEN": "private-meta-token"})
            self.assertNotIn(b"private-meta-token", raw)
        self.assertEqual(institutes.settings("victor-paranhos")["meta_account_id"], "123456")

    def test_meta_token_reuse_rejects_missing_unknown_or_conflicting_sources(self):
        self.store.create_first_master("Master", "master", "Master-test-password!")
        cookie = "doc4docs_session=" + self.store.login("master", "Master-test-password!")
        with patch.object(self.app, "save_config_values") as save:
            for payload in ({"values": {}, "meta_token_institute": "victor-paranhos"},
                            {"values": {}, "meta_token_institute": "unknown"},
                            {"values": {}, "meta_token_institute": ["victor-paranhos"]},
                            {"values": {"META_ACCESS_TOKEN": "other"}, "meta_token_institute": "victor-paranhos"}):
                with self.subTest(payload=payload):
                    self.assertEqual(self.request("POST", "/api/settings?clinic=vielle", payload, cookie,
                        {"X-DOC4DOCS-Request": "1"})[0], 400)
            save.assert_not_called()

    def test_master_assigns_crm_doctors_and_funnels_on_user_account(self):
        from crm_api import store_for
        crm = store_for(self.store)
        crm.bootstrap({"Marcela": ["Clinica"], "Victor Paranhos de Andrade": ["Permitido", "Restrito"]})
        victor = crm.listing()["doctor_id"]
        allowed, hidden = crm.listing(victor)["pipelines"]
        other = next(d["id"] for d in crm.listing()["doctors"] if d["id"] != victor)
        self.limit_permissions(["crm.view"])
        staff, _ = self.login()
        master = self.master_cookie()
        headers = {"X-DOC4DOCS-Request": "1"}
        path = f"/api/master/users/{self.user_id}/edit"
        scope = {victor: [allowed["id"]]}
        payload = {"nome": "Usuario Local", "login": "teste", "crm_scope": scope}
        self.assertEqual(self.request("POST", path, payload, staff, headers)[0], 403)
        self.assertEqual(self.request("POST", path, payload, master)[0], 403)
        status, _, raw = self.request("POST", path, payload, master, headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["user"]["crm_scope"], scope)
        status, _, raw = self.request("GET", "/api/auth/me", cookie=staff)
        self.assertEqual(json.loads(raw)["user"]["crm_scope"], scope)
        status, _, raw = self.request("GET", "/api/crm?clinic=vielle&view=kanban", cookie=staff)
        self.assertEqual(status, 200, raw)
        data = json.loads(raw)
        self.assertEqual([d["id"] for d in data["doctors"]], [victor])
        self.assertEqual([p["id"] for p in data["pipelines"]], [allowed["id"]])
        self.assertEqual(self.request("GET", f"/api/crm?clinic=vielle&doctor={other}", cookie=staff)[0], 403)
        self.assertEqual(self.request("GET", f"/api/crm?clinic=vielle&doctor={victor}&view=kanban&pipeline={hidden['id']}", cookie=staff)[0], 400)
        bad = {"nome": "Nao salvar", "login": "teste", "crm_scope": {other: [allowed["id"]]}}
        self.assertEqual(self.request("POST", path, bad, master, headers)[0], 400)
        self.assertEqual(self.store.get_public_user(self.user_id)["nome"], "Usuario Local")
        self.assertEqual(self.store.get_public_user(self.user_id)["crm_scope"], scope)
        status, _, raw = self.request("GET", "/api/master/users", cookie=master)
        self.assertEqual(status, 200, raw)
        self.assertEqual(len(json.loads(raw)["crm_catalog"]["doctors"]), 2)
        self.assertEqual(len(json.loads(raw)["crm_catalog"]["pipelines"]), 3)
        self.assertEqual(self.request("POST", path, {**payload, "crm_scope": {}}, master, headers)[0], 200)
        status, _, raw = self.request("GET", "/api/crm?clinic=vielle", cookie=staff)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["doctors"], [])
        self.assertEqual(self.request("POST", path, {**payload, "crm_scope": None}, master, headers)[0], 200)
        status, _, raw = self.request("GET", "/api/crm?clinic=vielle", cookie=staff)
        self.assertEqual(len(json.loads(raw)["doctors"]), 2)

    def test_crm_scope_blocks_hidden_read_write_media_and_mismatched_doctor_payload(self):
        from crm_api import store_for
        crm = store_for(self.store)
        crm.bootstrap({"Victor Paranhos de Andrade": ["Permitido", "Restrito"]})
        doctor = crm.listing()["doctor_id"]
        allowed, hidden = crm.listing(doctor)["pipelines"]
        lead = crm.lead({"doctor_id": doctor, "pipeline_id": hidden["id"], "stage_id": hidden["stages"][0]["id"],
                         "name": "Contato sintetico", "phone": "5500000000002", "title": "Restrito"}, "Master")
        contact = crm.detail(lead, doctor)["lead"]["contact_id"]
        channel = crm.channel({"doctor_id": doctor, "name": "Numero", "phone": "5500000000001"})
        self.limit_permissions(["crm.view"])
        cookie, _ = self.login()
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope={doctor: [allowed["id"]]})
        base = f"clinic=vielle&doctor={doctor}"
        for path in (f"/api/crm/lead?{base}&id={lead}", f"/api/crm/conversation?{base}&channel={channel}&contact={contact}",
                     f"/api/crm/patients?{base}", f"/api/crm/voice/history?{base}&lead={lead}"):
            self.assertEqual(self.request("GET", path, cookie=cookie)[0], 403, path)
        payload = {"doctor_id": doctor, "pipeline_id": hidden["id"], "stage_id": hidden["stages"][0]["id"],
                   "name": "Nao criar", "phone": "5500000000003", "title": "Restrito"}
        self.assertEqual(self.request("POST", f"/api/crm/lead?{base}", payload, cookie, {"X-DOC4DOCS-Request": "1"})[0], 403)
        payload.update(pipeline_id=allowed["id"], stage_id=allowed["stages"][0]["id"])
        self.assertEqual(self.request("POST", "/api/crm/lead?clinic=vielle", payload, cookie, {"X-DOC4DOCS-Request": "1"})[0], 403)
        status, _, raw = self.request("POST", f"/api/crm/lead?{base}", payload, cookie, {"X-DOC4DOCS-Request": "1"})
        self.assertEqual(status, 200, raw)

    def test_multi_doctor_inbox_respects_live_staff_permissions(self):
        from crm_api import store_for
        from urllib.parse import urlencode
        crm = store_for(self.store)
        crm.bootstrap({"Victor Paranhos de Andrade": ["Permitido", "Restrito"], "Outro doutor": ["Clinica"]})
        first = crm.listing()["doctor_id"]
        second = next(d["id"] for d in crm.listing()["doctors"] if d["id"] != first)
        allowed, hidden = crm.listing(first)["pipelines"]
        channels = []
        for index, doc in enumerate((first, second)):
            channel = crm.channel({"doctor_id": doc, "name": "Trafego" if index else "Organico", "phone": f"550000000000{index+1}"})
            channels.append(channel)
            with crm.db() as c:
                instance = crm.row(c, "channels", channel)["instance"]
            crm.receive(instance, {"key": {"remoteJid": "5500000000003@s.whatsapp.net", "id": f"received-{index}"},
                "message": {"conversation": "Mensagem sintetica"}}, "messages.upsert")
        with crm.db() as c:
            c.execute("UPDATE leads SET pipeline_id=?,stage_id=? WHERE doctor_id=?", (allowed["id"], allowed["stages"][0]["id"], first))
        path = "/api/crm/inbox-multi?" + urlencode({"clinic": "vielle", "doctor": first, "doctors": json.dumps([first, second])})
        cookie, _ = self.login()
        self.assertEqual(self.request("GET", path, cookie=cookie)[0], 403)
        self.limit_permissions(["crm.view"])
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope={first: [allowed["id"]], second: None})
        status, _, raw = self.request("GET", path, cookie=cookie)
        self.assertEqual(status, 200, raw)
        result = json.loads(raw)
        self.assertEqual(result["counts"]["all"], 2)
        self.assertEqual({r["doctor_id"] for r in result["conversations"]}, {first, second})
        self.assertFalse(any(p["id"] == hidden["id"] for p in result["pipelines"]))
        query = urlencode({"clinic": "vielle", "doctor": second, "channel": channels[1], "contact": result["conversations"][0]["contact_id"]})
        self.assertEqual(self.request("GET", "/api/crm/conversation?" + query, cookie=cookie)[0], 200)
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope={first: [allowed["id"]]})
        self.assertEqual(self.request("GET", path, cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/api/crm/conversation?" + query, cookie=cookie)[0], 403)
        for selection in ([], None, "all", [True]):
            bad = "/api/crm/inbox-multi?" + urlencode({"clinic": "vielle", "doctor": first, "doctors": json.dumps(selection)})
            self.assertEqual(self.request("GET", bad, cookie=cookie)[0], 400)

    def test_bulk_leads_require_permission_origin_scope_and_explicit_confirmation(self):
        from crm_api import store_for
        crm = store_for(self.store)
        crm.bootstrap({"Victor Paranhos de Andrade": ["Funil"]})
        doctor = crm.listing()["doctor_id"]
        pipeline = crm.listing(doctor)["pipelines"][0]
        lead = crm.lead({"doctor_id": doctor, "pipeline_id": pipeline["id"], "stage_id": pipeline["stages"][0]["id"],
            "name": "Sintetico", "phone": "5500000000002", "title": "Teste"}, "Equipe")
        cookie, _ = self.login()
        query = f"clinic=vielle&doctor={doctor}"
        payload = {"doctor_id": doctor, "pipeline_id": pipeline["id"], "action": "delete", "selection": {"lead_ids": [lead]}}
        headers = {"X-DOC4DOCS-Request": "1"}
        self.assertEqual(self.request("POST", f"/api/crm/lead-bulk-preview?{query}", payload, cookie, headers)[0], 403)
        self.limit_permissions(["crm.view"])
        self.assertEqual(self.request("POST", f"/api/crm/lead-bulk-preview?{query}", payload, cookie)[0], 403)
        status, _, raw = self.request("POST", f"/api/crm/lead-bulk-preview?{query}", payload, cookie, headers)
        self.assertEqual(status, 200, raw)
        job = json.loads(raw)["id"]
        action = {"doctor_id": doctor, "id": job}
        self.assertEqual(self.request("POST", f"/api/crm/lead-bulk-apply?{query}", action, cookie, headers)[0], 400)
        status, _, raw = self.request("POST", f"/api/crm/lead-bulk-apply?{query}", {**action, "confirm": True}, cookie, headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(self.request("GET", f"/api/crm/lead?{query}&id={lead}", cookie=cookie)[0], 404)
        status, _, raw = self.request("GET", f"/api/crm/lead-trash?{query}&pipeline={pipeline['id']}", cookie=cookie)
        self.assertEqual(json.loads(raw)["total"], 1)

    def test_channel_name_and_new_lead_destination_are_master_only(self):
        from crm_api import store_for
        crm = store_for(self.store)
        crm.bootstrap({"Victor Paranhos de Andrade": ["Funil"]})
        doctor = crm.listing()["doctor_id"]
        pipeline = crm.listing(doctor)["pipelines"][0]
        channel = crm.channel({"doctor_id": doctor, "name": "Antigo", "phone": "5500000000001"})
        self.limit_permissions(["crm.view"])
        staff, _ = self.login()
        master = self.master_cookie()
        path = f"/api/crm/channel-update?clinic=vielle&doctor={doctor}"
        payload = {"doctor_id": doctor, "id": channel, "config_revision": 1, "name": "Comercial",
                   "inbound_pipeline_id": pipeline["id"], "inbound_stage_id": pipeline["stages"][0]["id"]}
        headers = {"X-DOC4DOCS-Request": "1"}
        self.assertEqual(self.request("POST", path, payload, staff, headers)[0], 403)
        self.assertEqual(self.request("POST", path, payload, master)[0], 403)
        status, _, raw = self.request("POST", path, payload, master, headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(self.request("POST", path, payload, master, headers)[0], 409)

    def test_channel_transfer_requires_master_origin_and_explicit_confirmation(self):
        from crm_api import store_for
        crm = store_for(self.store)
        crm.bootstrap({"Victor Paranhos de Andrade": ["Funil origem"], "Outra doutora": ["Destino"]})
        doctor = crm.listing()["doctor_id"]
        target = next(d["id"] for d in crm.listing()["doctors"] if d["id"] != doctor)
        pipeline = crm.listing(target)["pipelines"][0]
        channel = crm.channel({"doctor_id": doctor, "name": "Atendimento", "phone": "5500000000001"})
        self.limit_permissions(["crm.view"])
        staff, _ = self.login()
        master = self.master_cookie()
        path = f"/api/crm/channel-transfer?clinic=vielle&doctor={doctor}"
        headers = {"X-DOC4DOCS-Request": "1"}
        payload = {"doctor_id": doctor, "id": channel, "config_revision": 1, "target_doctor_id": target,
                   "inbound_pipeline_id": pipeline["id"], "inbound_stage_id": pipeline["stages"][0]["id"], "confirm": True}
        self.assertEqual(self.request("GET", path + f"&id={channel}", cookie=staff)[0], 403)
        self.assertEqual(self.request("POST", path, payload, staff, headers)[0], 403)
        status, _, raw = self.request("GET", path + f"&id={channel}", cookie=master)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["channel"]["id"], channel)
        self.assertEqual(self.request("POST", path, payload, master)[0], 403)
        self.assertEqual(self.request("POST", path, {**payload, "confirm": False}, master, headers)[0], 400)
        self.assertEqual(self.request("POST", path, {**payload, "doctor_id": target}, master, headers)[0], 400)
        status, _, raw = self.request("POST", path, payload, master, headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["doctor_id"], target)
        self.assertEqual(len(self.store.list_audit(action="crm_channel-transfer")["events"]), 1)
        status, _, raw = self.request("GET", f"/api/crm?clinic=vielle&doctor={doctor}", cookie=master)
        self.assertEqual(json.loads(raw)["channels"], [])

    def test_crm_channel_removal_restore_master_only_csrf_and_preserved_history(self):
        from crm_api import store_for
        crm = store_for(self.store)
        crm.bootstrap({"Victor Paranhos de Andrade": ["Organico"], "Outra doutora": ["Outro"]})
        doctor = crm.listing()["doctor_id"]
        channel = crm.channel({"doctor_id": doctor, "name": "Atendimento", "phone": "5500000000001"})
        with crm.db() as c:
            instance = crm.row(c, "channels", channel)["instance"]
        crm.receive(instance, {"key": {"remoteJid": "5500000000002@s.whatsapp.net", "id": "preserved"},
            "pushName": "Contato sintetico", "message": {"conversation": "Historico preservado"}}, "messages.upsert")
        self.limit_permissions(["crm.view"])
        staff, _ = self.login()
        master = self.master_cookie()
        headers = {"X-DOC4DOCS-Request": "1"}
        path = f"/api/crm/channel-remove?clinic=vielle&doctor={doctor}"
        restore = path.replace("channel-remove", "channel-restore")
        payload = {"doctor_id": doctor, "id": channel, "config_revision": 1, "confirm": True}
        for action in (path, restore):
            self.assertEqual(self.request("POST", action, payload, staff, headers)[0], 403)
        self.assertEqual(self.request("GET", path + f"&id={channel}", cookie=staff)[0], 403)
        self.assertEqual(self.request("POST", path, payload, master)[0], 403)
        self.assertEqual(self.request("POST", path, {**payload, "confirm": False}, master, headers)[0], 400)
        self.assertEqual(self.request("GET", path + f"&id={channel}", cookie=master)[0], 200)
        status, _, raw = self.request("POST", path, payload, master, headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["state"], "archived")
        for action in ("connect", "channel-state"):
            self.assertEqual(self.request("POST", path.replace("channel-remove", action), payload, master, headers)[0], 400)
        self.assertEqual(len(crm.listing(doctor)["leads"]), 1)
        self.assertEqual(len(crm.inbox(doctor, "master", channel)["conversations"]), 1)
        status, _, raw = self.request("POST", restore, {**payload, "config_revision": 2}, master, headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["state"], "close")
        self.assertEqual(len(self.store.list_audit(action="crm_channel-remove")["events"]), 1)
        self.assertEqual(len(self.store.list_audit(action="crm_channel-restore")["events"]), 1)

    def test_staff_can_transfer_leads_only_between_granted_funnels(self):
        from crm_api import store_for
        crm = store_for(self.store)
        crm.bootstrap({"Victor Paranhos de Andrade": ["Origem"], "Outra doutora": ["Destino"]})
        source = crm.listing()["doctor_id"]
        target = next(d["id"] for d in crm.listing()["doctors"] if d["id"] != source)
        origin = crm.listing(source)["pipelines"][0]
        destination = crm.listing(target)["pipelines"][0]
        lead = crm.lead({"doctor_id": source, "pipeline_id": origin["id"], "stage_id": origin["stages"][0]["id"],
            "phone": "5500000000001", "name": "Contato sintetico", "title": "Encaminhar"}, "Equipe")
        self.limit_permissions(["crm.view"])
        staff, _ = self.login()
        headers = {"X-DOC4DOCS-Request": "1"}
        status, _, raw = self.request("GET", f"/api/crm/lead-transfer-options?clinic=vielle&doctor={source}", cookie=staff)
        self.assertEqual(status, 200, raw)
        self.assertIn(destination["id"], [p["id"] for p in json.loads(raw)["pipelines"]])
        payload = {"doctor_id": source, "pipeline_id": origin["id"], "action": "transfer", "selection": {"lead_ids": [lead]},
                   "target_doctor_id": target, "target_pipeline_id": destination["id"], "stage_id": destination["stages"][1]["id"]}
        path = f"/api/crm/lead-bulk-preview?clinic=vielle&doctor={source}"
        self.assertEqual(self.request("POST", path, payload, staff)[0], 403)
        source_scope = {source: [origin["id"]]}
        full_scope = {**source_scope, target: [destination["id"]]}
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope=source_scope)
        self.assertEqual(self.request("POST", path, payload, staff, headers)[0], 403)
        status, _, raw = self.request("GET", f"/api/crm/lead-transfer-options?clinic=vielle&doctor={source}", cookie=staff)
        self.assertNotIn(destination["id"], [p["id"] for p in json.loads(raw)["pipelines"]])
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope=full_scope)
        status, _, raw = self.request("POST", path, payload, staff, headers)
        self.assertEqual(status, 200, raw)
        batch = json.loads(raw)["id"]
        commit = f"/api/crm/lead-bulk-apply?clinic=vielle&doctor={source}"
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope=source_scope)
        self.assertEqual(self.request("POST", commit, {"id": batch, "doctor_id": source, "confirm": True}, staff, headers)[0], 403)
        self.store.update_user(self.user_id, "Usuario Local", "teste", crm_scope=full_scope)
        status, _, raw = self.request("POST", commit, {"id": batch, "doctor_id": source, "confirm": True}, staff, headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["doctor_id"], target)
        self.assertEqual(crm.detail(lead, target)["lead"]["pipeline_id"], destination["id"])
        self.assertEqual(len(self.store.list_audit(action="crm_lead-bulk-apply")["events"]), 1)

    def master_cookie(self):
        self.store.create_first_master("Master", "master", "Master-test-password!")
        return "doc4docs_session=" + self.store.login("master", "Master-test-password!")

    def test_admin_create_edit_disable_enable_reset_and_list(self):
        cookie=self.master_cookie()
        headers={"X-DOC4DOCS-Request":"1"}
        payload={"nome":"Pessoa Teste","login":"pessoa","password":"Pessoa-test-password!"}
        status,_,body=self.request("POST","/api/master/users",payload,cookie,headers)
        self.assertEqual(status,201,body)
        self.assertNotIn(b"password",body)
        user=json.loads(body)["user"]
        self.assertEqual(user["is_master"],0)
        prefix=f"/api/master/users/{user['id']}"
        self.assertEqual(self.request("POST",prefix+"/edit",{"nome":"Nome Novo","login":"novo"},cookie,headers)[0],200)
        self.assertEqual(self.request("POST",prefix+"/status",{"active":False},cookie,headers)[0],200)
        self.assertIsNone(self.store.login("novo","Pessoa-test-password!"))
        self.assertEqual(self.request("POST",prefix+"/status",{"active":True},cookie,headers)[0],200)
        token=self.store.login("novo","Pessoa-test-password!")
        self.assertEqual(self.request("POST",prefix+"/password",{"password":"Changed-test-password!"},cookie,headers)[0],200)
        self.assertIsNone(self.store.session_user(token))
        self.assertFalse(self.store.validate_password("novo","Pessoa-test-password!"))
        self.assertTrue(self.store.validate_password("novo","Changed-test-password!"))
        status,_,body=self.request("GET","/api/master/users?search=novo",cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)["total"],1)
        self.assertNotIn(b"password",body)

    def test_admin_payload_validation_and_no_master_promotion(self):
        cookie=self.master_cookie(); headers={"X-DOC4DOCS-Request":"1"}
        normal={"nome":"Teste","login":"novo","password":"Changed-test-password!"}
        for changed in ({"is_master":True},{"status":"other"},{"password":"short"},{"login":"has space"},{"nome":""}):
            self.assertEqual(self.request("POST","/api/master/users",{**normal,**changed},cookie,headers)[0],400)
        self.assertEqual(self.request("POST","/api/master/users",normal,cookie)[0],403)
        self.assertEqual(self.request("POST","/api/master/users",{**normal,"login":"teste"},cookie,headers)[0],409)
        self.assertEqual(self.request("POST",f"/api/master/users/{self.user_id}/edit",{"nome":"Test","login":"teste","is_master":True},cookie,headers)[0],400)
        self.assertEqual(self.request("POST","/api/master/users/999/status",{"active":False},cookie,headers)[0],404)
        self.assertEqual(self.store.get_user_by_login("teste")["is_master"],0)

    def test_master_promotion_requires_master_origin_confirmation_and_exact_target(self):
        cookie=self.master_cookie()
        path=f"/api/master/users/{self.user_id}/promote"
        payload={"confirm":True,"expected_login":"teste"}
        headers={"X-DOC4DOCS-Request":"1"}
        self.assertEqual(self.request("POST",path,payload,self.login()[0],headers)[0],403)
        self.assertEqual(self.request("POST",path,payload,cookie)[0],403)
        self.assertEqual(self.request("POST",path,{**payload,"confirm":False},cookie,headers)[0],400)
        self.assertEqual(self.request("POST",path,{**payload,"expected_login":"other"},cookie,headers)[0],400)
        self.assertEqual(self.request("POST",path,{**payload,"is_master":True},cookie,headers)[0],400)
        old_token=self.store.login("teste","Temporary-test-password!")
        status,_,raw=self.request("POST",path,payload,cookie,headers)
        self.assertEqual(status,200,raw)
        self.assertTrue(json.loads(raw)["user"]["is_master"])
        self.assertIsNone(self.store.session_user(old_token))
        self.assertEqual(self.request("GET","/api/master/users",cookie=cookie)[0],200)
        self.assertEqual(self.request("GET","/api/master/users",cookie=self.login()[0])[0],200)

    def test_native_quality_http_pilot_is_master_only_and_never_auto_enabled(self):
        cookie=self.master_cookie()
        from crm_api import store_for
        store=store_for(self.store)
        store.bootstrap({"Profissional Exemplo":["Clínica"]})
        doc=store.listing()["doctor_id"]
        path=f"/api/crm/quality?clinic=vielle&doctor={doc}"
        self.assertEqual(self.request("GET",path,cookie=self.login()[0])[0],403)
        status,_,raw=self.request("GET",path,cookie=cookie)
        self.assertEqual(status,200,raw)
        self.assertFalse(json.loads(raw)["settings"]["enabled"])
        settings=path.replace("/quality?","/quality/settings?")
        payload={"doctor_id":doc,"revision":0,"enabled":True,"auto_closed":False,"daily_limit":20,"confirm":True}
        self.assertEqual(self.request("POST",settings,payload,cookie)[0],403)
        self.assertEqual(self.request("POST",settings,{**payload,"auto_closed":True},cookie,{"X-DOC4DOCS-Request":"1"})[0],400)
        status,_,raw=self.request("POST",settings,payload,cookie,{"X-DOC4DOCS-Request":"1"})
        self.assertEqual(status,200,raw)
        self.assertEqual(json.loads(raw)["settings"]["daily_limit"],20)
        with store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM quality_jobs").fetchone()[0],0)

    def test_clinic_journey_and_nested_opportunities_http_boundaries(self):
        cookie=self.master_cookie();headers={"X-DOC4DOCS-Request":"1"}
        from crm_api import store_for
        store=store_for(self.store)
        store.bootstrap({"Profissional Exemplo":["Clínica", "RegenCode"]})
        data=store.listing();doc=data["doctor_id"];pipeline=next(p for p in data["pipelines"] if p["name"]=="Clínica")
        ident=store.lead({"doctor_id":doc,"pipeline_id":pipeline["id"],"stage_id":pipeline["stages"][0]["id"],
            "name":"Pessoa Exemplo","phone":"5500000000007","title":"Negociação de exemplo"},"Equipe")
        path=f"/api/crm/clinic-journey/preview?clinic=vielle&doctor={doc}"
        payload={"doctor_id":doc,"pipeline_id":pipeline["id"]}
        self.assertEqual(self.request("POST",path,payload,cookie)[0],403)
        status,_,raw=self.request("POST",path,payload,cookie,headers)
        self.assertEqual(status,200,raw)
        preview=json.loads(raw)
        status,_,raw=self.request("POST",path.replace("/preview?","/apply?"),{"doctor_id":doc,"id":preview["id"],"confirm":True},cookie,headers)
        self.assertEqual(status,200,raw)
        deal_path=f"/api/crm/patient-opportunities?clinic=vielle&doctor={doc}"
        self.assertEqual(self.request("GET",deal_path+"&lead="+ident,cookie=cookie)[0],200)
        self.assertEqual(self.request("POST",deal_path,{"doctor_id":doc,"lead_id":{}},cookie,headers)[0],400)
        self.limit_permissions(["crm.view"])
        staff=self.login()[0]
        self.assertEqual(self.request("POST",path,payload,staff,headers)[0],403)
        self.assertEqual(self.request("GET",deal_path+"&lead="+ident,cookie=staff)[0],200)

    def test_admin_refuses_last_master_deactivation(self):
        cookie=self.master_cookie();uid=self.store.get_user_by_login("master")["id"]
        self.assertEqual(self.request("POST",f"/api/master/users/{uid}/status",{"active":False},cookie,{"X-DOC4DOCS-Request":"1"})[0],400)
        self.assertEqual(self.request("GET","/api/master/users",cookie=cookie)[0],200)

    def test_master_team_profile_is_scoped_audited_and_does_not_grant_permissions(self):
        cookie=self.master_cookie(); headers={"X-DOC4DOCS-Request":"1"}
        before=self.store.get_public_user(self.user_id)
        payload={"nome":before["nome"],"login":before["login"],"team_profiles":{"vielle":"management"}}
        path=f"/api/master/users/{self.user_id}/edit"
        self.assertEqual(self.request("POST",path,payload,cookie)[0],403)
        status,_,body=self.request("POST",path,payload,cookie,headers)
        self.assertEqual(status,200,body)
        user=json.loads(body)["user"]
        self.assertEqual(user["team_profiles"],{"vielle":"management"})
        self.assertEqual(user["permissions"],before["permissions"])
        self.assertEqual(self.request("POST",path,{**payload,"team_profiles":{"carla":"management"}},cookie,headers)[0],400)
        self.assertEqual(self.request("POST",path,{**payload,"team_profiles":None},cookie,headers)[0],400)
        self.assertTrue(self.store.list_audit(action="team_profiles_changed")["events"])
        regular,_=self.login()
        self.assertEqual(self.request("POST",path,payload,regular,headers)[0],403)

    def test_master_can_set_own_team_profile_without_changing_account_access(self):
        cookie=self.master_cookie(); headers={"X-DOC4DOCS-Request":"1"}
        master=self.store.get_user_by_login("master")
        path=f"/api/master/users/{master['id']}/edit"
        status,_,body=self.request("POST",path,{"nome":"Master","login":"master","team_profiles":{"vielle":"attendant"}},cookie,headers)
        self.assertEqual(status,200,body)
        user=json.loads(body)["user"]
        self.assertTrue(user["is_master"])
        self.assertEqual(set(user["clinic_keys"]),set(SUPPORTED_CLINICS))
        self.assertEqual(user["team_profiles"]["vielle"],"attendant")

    def test_master_creates_user_with_team_profile(self):
        cookie=self.master_cookie(); headers={"X-DOC4DOCS-Request":"1"}
        payload={"nome":"Gestao Teste","login":"gestaoteste","password":"Temporary-test-password!",
                 "clinic_keys":["vielle"],"permissions":{"vielle":[]},"team_profiles":{"vielle":"management"}}
        status,_,body=self.request("POST","/api/master/users",payload,cookie,headers)
        self.assertEqual(status,201,body)
        self.assertEqual(json.loads(body)["user"]["team_profiles"],{"vielle":"management"})

    def test_clinic_isolation_in_pages_and_apis(self):
        cookie, _ = self.login()
        headers = {"X-DOC4DOCS-Request": "1"}
        for path in ("/?clinic=inspire", "/index.html?clinic=carla", "/static/index.html?clinic=inspire",
                     "/api/report?clinic=inspire", "/api/export-pdf?clinic=carla", "/kommo-widget?clinic=inspire",
                     "/auth/start?clinic=carla", "/api/sync?clinic=inspire", "/api/sync-clinica?clinic=carla",
                     "/api/sync-traffic?clinic=inspire", "/api/monthly-goal?clinic=carla"):
            self.assertEqual(self.request("GET", path, cookie=cookie, headers=headers)[0], 403, path)
        self.assertEqual(self.request("HEAD", "/index.html?clinic=carla", cookie=cookie)[0], 403)
        for path in ("/api/monthly-goal", "/api/patient-followup-contact", "/api/patient-followup-status",
                     "/api/quote-followup-contact", "/api/quote-followup-status", "/api/whatsapp-audit-review",
                     "/api/whatsapp-audit-ai", "/api/clinica-patient-link"):
            self.assertEqual(self.request("POST", path+"?clinic=inspire", {}, cookie, headers)[0], 403, path)
        self.report.assert_not_called()
        status, _, body = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([item["key"] for item in json.loads(body)["clinics"]], ["vielle"])

    def test_clinic_keys_fail_closed_and_default_scope(self):
        cookie, _ = self.login()
        for query in ("clinic=unknown", "clinic=", "clinic=vielle&clinic=inspire", "clinic=VIELLE"):
            self.assertEqual(self.request("GET", "/api/report?"+query, cookie=cookie)[0], 400)
        self.store.update_user(self.user_id, "Usuario Local", "teste", clinic_keys=["inspire"])
        for path in ("/api/report", "/kommo-widget", "/acompanhamento", "/auth/start"):
            self.assertEqual(self.request("GET", path, cookie=cookie)[0], 403, path)
        self.assertEqual(self.request("GET", "/", cookie=cookie)[0], 200)
        self.report.assert_not_called()

    def test_legacy_clinic_codes_cannot_override_membership(self):
        cookie, _ = self.login()
        headers = {"X-DOC4DOCS-Request": "1"}
        with patch.object(self.app, "clinic_access_code", return_value="known-code"):
            for mode in ("dashboard", "team"):
                status, _, _ = self.request("POST", "/api/clinic-access", {"clinic_id":"carla", "access_code":"known-code", "access_mode":mode}, cookie, headers)
                self.assertEqual(status, 403)
            status, response_headers, _ = self.request("POST", "/api/clinic-access", {"clinic_id":"vielle", "access_code":"known-code"}, cookie, headers)
            self.assertEqual(status, 200)
            self.assertFalse(any(key == "Set-Cookie" for key, value in response_headers))
            old_cookie = self.app.clinic_access_cookie("vielle").split(";",1)[0]
            self.assertEqual(self.request("GET", "/api/report?clinic=vielle", cookie=cookie+"; "+old_cookie)[0], 200)
            self.store.update_user(self.user_id, "Usuario Local", "teste", clinic_keys=[])
            self.assertEqual(self.request("GET", "/api/report?clinic=vielle", cookie=cookie+"; "+old_cookie)[0], 403)
            self.assertEqual(self.request("GET", "/api/auth/me", cookie=cookie)[0], 200)

    def test_master_assigns_clinics_and_common_cannot(self):
        master = self.master_cookie()
        common, _ = self.login()
        headers = {"X-DOC4DOCS-Request": "1"}
        path = f"/api/master/users/{self.user_id}/edit"
        data = {"nome":"Usuario Local", "login":"teste", "clinic_keys":["inspire", "carla"]}
        self.assertEqual(self.request("POST", path, data, common, headers)[0], 403)
        self.assertEqual(self.store.allowed_clinics(self.user_id), ["vielle"])
        self.assertEqual(self.request("POST", path, data, master, headers)[0], 200)
        self.assertEqual(self.store.allowed_clinics(self.user_id), ["inspire", "carla"])
        for invalid in (["bad"], "vielle", None, [42]):
            self.assertEqual(self.request("POST", path, {**data, "clinic_keys":invalid}, master, headers)[0], 400)
        status, _, body = self.request("POST", "/api/master/users", {"nome":"Created", "login":"created", "password":"Temporary-test-password!", "clinic_keys":["carla"]}, master, headers)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["user"]["clinic_keys"], ["carla"])
        _, _, body = self.request("GET", "/api/auth/me", cookie=master)
        self.assertEqual([item["key"] for item in json.loads(body)["clinics"]], list(SUPPORTED_CLINICS))

    def test_brandao_requires_explicit_membership_and_permissions(self):
        master = self.master_cookie()
        common, _ = self.login()
        for path in ("/?clinic=brandao", "/api/report?clinic=brandao"):
            self.assertEqual(self.request("GET", path, cookie=common)[0], 403)
        _, _, body = self.request("GET", "/api/auth/me", cookie=master)
        self.assertIn({"key": "brandao", "name": "Clínica Brandão"}, json.loads(body)["clinics"])
        data = {"nome": "Usuario Local", "login": "teste", "clinic_keys": ["brandao"],
                "permissions": {"brandao": ["dashboard.view"]}}
        path = f"/api/master/users/{self.user_id}/edit"
        status, _, body = self.request("POST", path, data, master, {"X-DOC4DOCS-Request": "1"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request("GET", "/api/report?clinic=brandao", cookie=common)[0], 200)
        self.assertEqual(self.request("GET", "/api/report?clinic=vielle", cookie=common)[0], 403)
        self.assertEqual(self.request("GET", "/api/report?clinic=brandao&view=financialView", cookie=common)[0], 403)
        self.assertEqual(self.request("GET", "/settings.html?clinic=brandao", cookie=common)[0], 403)

    def limit_permissions(self, permissions):
        self.store.update_user(self.user_id,"Usuario Local","teste",clinic_keys=["vielle"],permissions={"vielle":permissions})

    def test_refresh_is_available_to_readonly_users_in_their_clinic(self):
        self.limit_permissions(["commercial.view"])
        cookie, _ = self.login()
        headers = {"X-DOC4DOCS-Request": "1"}
        safe_result = {"ok": True, "clinic": "vielle", "running": True, "started": True}
        with patch.object(self.app, "DASHBOARD_REFRESH_JOBS") as jobs, patch.object(self.app, "refresh_clinic_integrations") as refresh:
            jobs.start.return_value = safe_result
            jobs.status.return_value = safe_result
            status, _, body = self.request("POST", "/api/refresh?clinic=vielle",
                {"date_from": "2026-09-01", "date_to": "2026-09-30"}, cookie, headers)
            self.assertEqual(status, 202, body)
            self.assertEqual(json.loads(body), safe_result)
            self.assertEqual(jobs.start.call_args.args[0], "vielle")
            progress = lambda name: None
            jobs.start.call_args.args[1](progress)
            refresh.assert_called_once_with("vielle", "2026-09-01", "2026-09-30", progress)
            self.assertEqual(self.request("GET", "/api/refresh?clinic=vielle", cookie=cookie)[0], 200)
            jobs.status.assert_called_once_with("vielle")
        self.assertEqual(self.request("GET", "/api/settings?clinic=vielle", cookie=cookie)[0], 403)
        self.assertEqual(self.request("POST", "/api/sync-all?clinic=vielle", {}, cookie, headers)[0], 403)

    def test_refresh_requires_session_membership_and_csrf(self):
        path = "/api/refresh?clinic=vielle"
        headers = {"X-DOC4DOCS-Request": "1"}
        with patch.object(self.app, "DASHBOARD_REFRESH_JOBS") as jobs:
            for method in ("GET", "POST"):
                self.assertEqual(self.request(method, path)[0], 401)
            cookie, _ = self.login()
            for method in ("GET", "POST"):
                for query, code in (("?clinic=inspire", 403), ("", 400), ("?clinic=", 400),
                                    ("?clinic=vielle&clinic=inspire", 400), ("?clinic=invalid", 400)):
                    self.assertEqual(self.request(method, "/api/refresh" + query, cookie=cookie, headers=headers)[0], code, query)
            self.assertEqual(self.request("POST", path, {}, cookie)[0], 403)
            self.assertEqual(self.request("POST", path, {}, cookie, {**headers, "Origin": "https://other.test"})[0], 403)
            self.assertEqual(self.request("HEAD", path, cookie=cookie)[0], 405)
            self.store.set_user_active(self.user_id, False)
            self.assertEqual(self.request("POST", path, {}, cookie, headers)[0], 401)
            jobs.start.assert_not_called()
            jobs.status.assert_not_called()

    def test_refresh_rejects_reset_flags_and_invalid_periods(self):
        cookie, _ = self.login()
        headers = {"X-DOC4DOCS-Request": "1"}
        path = "/api/refresh?clinic=vielle"
        with patch.object(self.app, "DASHBOARD_REFRESH_JOBS") as jobs:
            for payload in ({"reset_data": True}, {"reset_oauth": True}, {"clinic": "inspire"},
                            {"date_from": "2026-09-01"}, {"date_from": "2026-09-30", "date_to": "2026-09-01"},
                            {"date_from": "2025-01-01", "date_to": "2026-09-30"}, "[]", "invalid"):
                self.assertEqual(self.request("POST", path, payload, cookie, headers)[0], 400, payload)
            self.assertEqual(self.request("POST", path, "", cookie, {**headers, "Content-Length": "-1"})[0], 400)
            self.assertEqual(self.request("POST", path, " " * 1025, cookie, headers)[0], 400)
            jobs.start.assert_not_called()
            jobs.start.side_effect = RuntimeError("private backend detail")
            status, _, body = self.request("POST", path, {}, cookie, headers)
            self.assertEqual(status, 503)
            self.assertNotIn(b"private backend detail", body)

    def test_excel_export_permissions_and_response(self):
        path = "/api/export-chart?clinic=vielle&chart=revenue_daily"
        self.assertEqual(self.request("GET", path)[0], 401)
        cookie, _ = self.login()
        self.limit_permissions(["dashboard.view", "commercial.view", "commercial.export"])
        self.assertEqual(self.request("GET", path + "&view=commercialView", cookie=cookie)[0], 403)
        self.report.assert_not_called()
        self.limit_permissions(["dashboard.view", "dashboard.export"])
        self.report.return_value = {"general_panel": {}}
        status, headers, data = self.request("GET", path, cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertTrue(data.startswith(b"PK"))
        self.assertIn("spreadsheetml", dict(headers)["Content-Type"])
        self.assertEqual(dict(headers)["Cache-Control"], "no-store")
        self.assertEqual(self.report.call_args.kwargs["export_chart"], "revenue_daily")
        self.assertEqual(self.request("GET", path.replace("vielle", "inspire"), cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", path.replace("revenue_daily", "invalid"), cookie=cookie)[0], 400)

    def test_report_endpoint_projects_only_authorized_view(self):
        self.limit_permissions(["commercial.view"])
        cookie,_=self.login()
        self.report.return_value={"connected":True,"totals":{"total_leads":7},"financial":{"secret":"finance", "sales_intelligence":{"top_patients":[{"patient":"Teste","amount":100}], "private_details":"must not leak"}},"general_panel":{"secret":"summary"},"whatsapp_audit":{"secret":"chats"},"patient_followup":{"secret":"patients"},"quote_followup":{"secret":"budgets"},"paid_traffic":{"secret":"ads"}}
        status,_,body=self.request("GET","/api/report?clinic=vielle&view=commercialView",cookie=cookie)
        self.assertEqual(status,200);data=json.loads(body)
        self.assertEqual(data["totals"]["total_leads"],7)
        self.assertEqual(set(data),{"connected","totals","sales_intelligence"})
        self.assertEqual(data["sales_intelligence"], {"top_patients":[{"patient":"Teste","amount":100}]})
        self.report.reset_mock()
        for query in ("view=financialView","include_followup=1","view=whatsappAuditView","view=generalView"):
            self.assertEqual(self.request("GET","/api/report?clinic=vielle&"+query,cookie=cookie)[0],403)
        self.assertEqual(self.request("GET","/api/report?clinic=vielle&view=commercialView&include_whatsapp_audit=1",cookie=cookie)[0],400)
        self.report.assert_not_called()

    def test_competence_report_permissions_and_export(self):
        path = "/api/financial-competence?clinic=vielle&date_from=2026-09-01&date_to=2026-09-30"
        export_path = path.replace("?", "/export?")
        self.assertEqual(self.request("GET", path)[0], 401)
        cookie, _ = self.login()
        self.limit_permissions(["commercial.view"])
        self.assertEqual(self.request("GET", path, cookie=cookie)[0], 403)
        self.limit_permissions(["financial.view"])
        self.assertEqual(self.request("GET", export_path, cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", path.replace("vielle", "inspire"), cookie=cookie)[0], 403)
        with patch.object(self.app, "db"), \
                patch.object(self.app, "clinic_doctor_professionals", return_value={"Teste A": "a"}), \
                patch.object(self.app, "forced_professional_uuids", return_value=[]), \
                patch("financial_competence.build_report", return_value={"items": [], "totals": {"income": 0, "expense": 0, "balance": 0,
                      "income_gross": 0, "expense_gross": 0, "balance_gross": 0},
                      "settlement_totals": {"income_received": 0, "expense_paid": 0, "income_open": 0, "expense_open": 0},
                      "basis": "teste", "excluded": {}, "pending": []}) as build:
            self.assertEqual(self.request("GET", path, cookie=cookie)[0], 200)
            self.assertEqual(build.call_args.args[2], [])
            self.assertEqual(self.request("GET", path + "&doctor=Teste%20A", cookie=cookie)[0], 200)
            self.assertEqual(build.call_args.args[2], ["a"])
            self.assertEqual(self.request("GET", path + "&date_basis=receipt&payment_status=partial", cookie=cookie)[0], 200)
            self.assertEqual(build.call_args.args[1]["date_basis"], "receipt")
            self.assertEqual(build.call_args.args[1]["payment_status"], "partial")
            self.assertEqual(self.request("GET", path + "&date_basis=invalid", cookie=cookie)[0], 400)
            self.assertEqual(self.request("GET", path + "&doctor=unknown", cookie=cookie)[0], 400)
            self.assertEqual(self.request("GET", path.replace("2026-09-01", "2026-10-01"), cookie=cookie)[0], 400)
            self.limit_permissions(["financial.view", "financial.export"])
            status, headers, body = self.request("GET", export_path, cookie=cookie)
            self.assertEqual(status, 200, body)
            self.assertTrue(body.startswith(b"PK"))
            self.assertEqual(dict(headers)["Cache-Control"], "no-store")
            self.assertTrue(build.call_args.kwargs["export"])
            self.assertEqual(self.request("HEAD", path, cookie=cookie)[0], 405)
            with patch("financial_competence.build_report", side_effect=RuntimeError("private details")):
                status, _, body = self.request("GET", path, cookie=cookie)
                self.assertEqual(status, 500)
                self.assertNotIn(b"private details", body)

    def test_view_url_export_and_action_permissions(self):
        self.limit_permissions(["patient_followup.view","commercial.view"])
        cookie,_=self.login();headers={"X-DOC4DOCS-Request":"1"}
        self.assertEqual(self.request("GET","/?clinic=vielle&view=financialView",cookie=cookie)[0],403)
        self.assertEqual(self.request("GET","/static/index.html?clinic=vielle&view=financialView",cookie=cookie)[0],403)
        self.assertEqual(self.request("GET","/?clinic=vielle&view=commercialView&print=1",cookie=cookie)[0],403)
        self.assertEqual(self.request("GET","/api/export-pdf?clinic=vielle&view=commercialView",cookie=cookie)[0],403)
        self.assertEqual(self.request("GET","/api/export-authorize?clinic=vielle&view=commercialView",cookie=cookie)[0],403)
        for path in ("/api/patient-followup-contact","/api/patient-followup-status","/api/quote-followup-contact","/api/quote-followup-status","/api/whatsapp-audit-review","/api/whatsapp-audit-ai","/api/monthly-goal"):
            self.assertEqual(self.request("POST",path+"?clinic=vielle",{},cookie,headers)[0],403,path)
        with patch.object(self.app,"save_patient_followup_contact",return_value={"ok":True}) as saver:
            self.limit_permissions(["patient_followup.view","patient_followup.create"])
            self.assertEqual(self.request("POST","/api/patient-followup-contact?clinic=vielle",{},cookie,headers)[0],200)
            saver.assert_called_once()
        self.limit_permissions(["commercial.view","commercial.export"])
        self.assertEqual(self.request("GET","/api/export-pdf?clinic=vielle&view=commercialView",cookie=cookie)[0],302)
        self.assertEqual(self.request("GET","/api/export-authorize?clinic=vielle&view=commercialView",cookie=cookie)[0],200)

    def test_team_mode_is_not_a_permission_bypass(self):
        self.limit_permissions(["commercial.view"])
        cookie,_=self.login()
        for path in ("/acompanhamento","/api/report?clinic=vielle&view=patientFollowupView&modo=equipe","/?clinic=vielle&view=patientFollowupView&staff=1"):
            self.assertEqual(self.request("GET",path,cookie=cookie)[0],403,path)
        self.assertEqual(self.request("GET","/api/report?clinic=vielle&view=commercialView&modo=equipe",cookie=cookie)[0],200)

    def test_master_profiles_audit_and_common_denial(self):
        master=self.master_cookie();common,_=self.login();headers={"X-DOC4DOCS-Request":"1"}
        for path in ("/api/master/profiles","/api/master/audit"):
            self.assertEqual(self.request("GET",path,cookie=common)[0],403)
            self.assertEqual(self.request("POST",path,{"name":"Bad"},common,headers)[0],403)
        status,_,body=self.request("POST","/api/master/profiles",{"name":"Somente leitura","permissions":["commercial.view"]},master,headers)
        self.assertEqual(status,200);pid=json.loads(body)["id"]
        self.assertEqual(self.request("POST",f"/api/master/profiles/{pid}",{"name":"Leitura","permissions":["financial.view"]},master,headers)[0],200)
        self.assertEqual(self.request("POST","/api/master/profiles",{"name":"Invalid","permissions":["master.view"]},master,headers)[0],400)
        status,_,body=self.request("GET","/api/master/audit?action=profile_saved",cookie=master)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)["total"],2)
        self.assertNotIn(b"password_hash",body)
