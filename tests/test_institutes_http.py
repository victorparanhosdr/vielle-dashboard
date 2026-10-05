import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import app
from institute_api import store_for


class QuietHandler(app.Handler):
    def log_message(self,*args):pass


class InstituteHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.auth=app.AuthStore(Path(cls.tmp.name)/"auth.sqlite3")
        cls.auth.initialize()
        password="test-private-password"
        cls.auth.create_first_master("Master","master",password)
        cls.master=cls.auth.login("master",password)
        cls.uid=cls.auth.create_user("Sem clínica","institute",password)
        cls.member=cls.auth.login("institute",password)
        cls.clinicid=cls.auth.create_user("Clínica","clinic",password,clinic_keys=["vielle"],permissions={"vielle":["dashboard.view"]})
        cls.clinic=cls.auth.login("clinic",password)
        store_for(cls.auth).save_members("victor-paranhos",[cls.uid])
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),QuietHandler)
        cls.server.auth_store=cls.auth
        cls.server.login_limiter=app.LoginLimiter()
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()

    def request(self,path,token="",payload=None,origin=True):
        c=http.client.HTTPConnection("127.0.0.1",self.server.server_port)
        headers={"Cookie":"doc4docs_session="+token}
        if origin:headers["X-DOC4DOCS-Request"]="1"
        if payload is not None:headers["Content-Type"]="application/json"
        c.request("POST" if payload is not None else "GET",path,json.dumps(payload) if payload is not None else None,headers)
        response=c.getresponse();raw=response.read();status=response.status;c.close();return status,raw

    def test_anonymous_cannot_open_page_or_data(self):
        self.assertEqual(self.request("/institutes.html")[0],303)
        self.assertEqual(self.request("/api/institutes/report")[0],401)

    def test_clinic_membership_does_not_unlock_institute(self):
        self.assertEqual(self.request("/institutes.html",self.clinic)[0],200)
        self.assertEqual(json.loads(self.request("/api/institutes",self.clinic)[1])["institutes"],[])
        self.assertEqual(self.request("/institutes.html?institute=victor-paranhos",self.clinic)[0],403)
        self.assertEqual(self.request("/api/institutes/report?from=2026-09-01&to=2026-09-30",self.clinic)[0],403)

    def test_member_without_clinic_can_read_institute(self):
        self.assertEqual(self.request("/institutes.html",self.member)[0],200)
        self.assertEqual(self.request("/api/institutes/report?from=2026-09-01&to=2026-09-30",self.member)[0],200)

    def test_all_courses_report_and_export_keep_institute_authorization(self):
        for route in ("report", "export"):
            url = "/api/institutes/"+route+"?institute=victor-paranhos&course=all&from=2026-09-01&to=2026-09-30"
            self.assertEqual(self.request(url, self.member)[0], 200)
            self.assertEqual(self.request(url, self.clinic)[0], 403)

    def test_all_settings_read_but_campaign_and_lead_writes_rejected(self):
        base = "?institute=victor-paranhos&course=all"
        self.assertEqual(self.request("/api/institutes/settings"+base, self.master)[0], 200)
        self.assertEqual(self.request("/api/institutes/campaigns"+base, self.master, {"campaigns":[]})[0], 400)
        self.assertEqual(self.request("/api/institutes/import-leads"+base, self.master, {"csv":""})[0], 400)

    def test_admin_routes_forbidden_to_member(self):
        for route in ("settings","products","meta-campaigns","members","courses","campaigns","import-leads"):
            self.assertEqual(self.request("/api/institutes/"+route,self.member)[0],403)

    def test_static_alias_guard_and_clinic_query_rejected(self):
        self.assertEqual(self.request("/static/institutes.html?institute=victor-paranhos",self.clinic)[0],403)
        self.assertEqual(self.request("/institutes.html?clinic=vielle",self.master)[0],400)
        self.assertEqual(self.request("/api/institutes/report?institute=victor-paranhos&institute=other",self.master)[0],400)

    def test_csrf_and_unrecognized_institute(self):
        self.assertEqual(self.request("/api/institutes/settings",self.master,{"meta_account_id":"123"},False)[0],403)
        self.assertEqual(self.request("/api/institutes/settings?institute=other",self.master)[0],404)

    def test_revocation_takes_effect(self):
        store=store_for(self.auth)
        store.save_members("victor-paranhos",[])
        try:self.assertEqual(self.request("/api/institutes/report?from=2026-09-01&to=2026-09-30",self.member)[0],403)
        finally:store.save_members("victor-paranhos",[self.uid])

    def test_catalog_lists_future_institutes_only_when_granted(self):
        store=store_for(self.auth)
        with store.connection() as conn:
            conn.execute("INSERT INTO institutes VALUES (?,?)",("future-test","Instituto de teste"))
        try:
            master=json.loads(self.request("/api/institutes",self.master)[1])
            member=json.loads(self.request("/api/institutes",self.member)[1])
            self.assertIn("future-test",[row["key"] for row in master["institutes"]])
            self.assertEqual([row["key"] for row in member["institutes"]],["victor-paranhos"])
            self.assertEqual(self.request("/institutos?institute=future-test",self.member)[0],403)
        finally:
            with store.connection() as conn:conn.execute("DELETE FROM institutes WHERE key='future-test'")


if __name__=="__main__":unittest.main()
