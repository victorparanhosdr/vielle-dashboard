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
        self.user_id = self.store.create_user("Usuario Local", "teste", "Temporary-test-password!", clinic_keys=["vielle"])
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
        for path in ("/", "/index.html", "/static/index.html", "/settings.html", "/acompanhamento", "/kommo-widget", "/static/../index.html"):
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

    def test_public_login_and_assets(self):
        for path in ("/login", "/login.html", "/static/login.html", "/login.css", "/login.js", "/doc4docs-logo-white.png"):
            self.assertEqual(self.request("GET", path)[0], 200, path)
        for path in ("/doc4docs-favicon.png?v=20260908", "/static/doc4docs-favicon.png?v=20260908"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertEqual({key.lower(): value for key, value in headers}["content-type"], "image/png")
            self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))

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
        self.assertEqual(self.request("GET", "/", cookie=cookie)[0], 303)

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

    def test_admin_refuses_last_master_deactivation(self):
        cookie=self.master_cookie();uid=self.store.get_user_by_login("master")["id"]
        self.assertEqual(self.request("POST",f"/api/master/users/{uid}/status",{"active":False},cookie,{"X-DOC4DOCS-Request":"1"})[0],400)
        self.assertEqual(self.request("GET","/api/master/users",cookie=cookie)[0],200)

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
        self.assertEqual(len(json.loads(body)["clinics"]), 3)

    def limit_permissions(self, permissions):
        self.store.update_user(self.user_id,"Usuario Local","teste",clinic_keys=["vielle"],permissions={"vielle":permissions})

    def test_report_endpoint_projects_only_authorized_view(self):
        self.limit_permissions(["commercial.view"])
        cookie,_=self.login()
        self.report.return_value={"connected":True,"totals":{"total_leads":7},"financial":{"secret":"finance"},"general_panel":{"secret":"summary"},"whatsapp_audit":{"secret":"chats"},"patient_followup":{"secret":"patients"},"quote_followup":{"secret":"budgets"},"paid_traffic":{"secret":"ads"}}
        status,_,body=self.request("GET","/api/report?clinic=vielle&view=commercialView",cookie=cookie)
        self.assertEqual(status,200);data=json.loads(body)
        self.assertEqual(data["totals"]["total_leads"],7)
        self.assertEqual(set(data),{"connected","totals"})
        self.report.reset_mock()
        for query in ("view=financialView","include_followup=1","view=whatsappAuditView","view=generalView"):
            self.assertEqual(self.request("GET","/api/report?clinic=vielle&"+query,cookie=cookie)[0],403)
        self.assertEqual(self.request("GET","/api/report?clinic=vielle&view=commercialView&include_whatsapp_audit=1",cookie=cookie)[0],400)
        self.report.assert_not_called()

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
