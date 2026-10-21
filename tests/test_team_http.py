import contextlib
import http.client
from http.server import ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from auth_http import LoginLimiter
from auth_store import AuthStore


class TeamHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.env=patch.dict(os.environ,{"DATA_DIR":cls.tmp.name});cls.env.start()
        cls.app=importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.env.stop();cls.tmp.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.auth=AuthStore(Path(self.tmp.name)/"auth.sqlite3");self.auth.initialize()
        self.uid=self.auth.create_user("Ana","ana","Temporary-test-password!",clinic_keys=["vielle"],permissions={"vielle":["crm.view"]})
        self.manager=self.auth.create_user("Gestor","gestor","Temporary-test-password!",clinic_keys=["vielle"],permissions={"vielle":["team.view"]})
        self.other=self.auth.create_user("Outra Clínica","other","Temporary-test-password!",clinic_keys=["carla"],permissions={"carla":["team.view"]})
        self.cookie="doc4docs_session="+self.auth.login("ana","Temporary-test-password!")
        self.manager_cookie="doc4docs_session="+self.auth.login("gestor","Temporary-test-password!")
        for patcher in (patch.object(self.app,"clinic_context",side_effect=lambda _:contextlib.nullcontext()),
                        patch.object(self.app.Handler,"log_message",return_value=None),
                        patch("urllib.request.urlopen",side_effect=AssertionError("External network forbidden"))):
            patcher.start();self.addCleanup(patcher.stop)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),self.app.Handler)
        self.server.auth_store=self.auth;self.server.login_limiter=LoginLimiter()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,method,path,payload=None,cookie=None,csrf=True,extra=None):
        headers={"Cookie":self.cookie if cookie is None else cookie,**(extra or {})}
        if csrf:headers["X-DOC4DOCS-Request"]="1"
        if payload is not None:headers["Content-Type"]="application/json"
        conn=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        conn.request(method,path,body=json.dumps(payload) if payload is not None else None,headers=headers)
        r=conn.getresponse();status=r.status;raw=r.read();conn.close()
        return status,raw

    def test_staff_only_sees_current_presence_and_never_report_or_page(self):
        status,raw=self.request("GET","/api/team/presence?clinic=vielle")
        self.assertEqual(status,200,raw);data=json.loads(raw)
        self.assertFalse(data["can_manage"])
        self.assertEqual({m["id"] for m in data["members"]},{self.uid,self.manager})
        self.assertEqual(set(data["members"][0]),{"id","name","status","status_since"})
        for path in ("/api/team/report?clinic=vielle","/team.html?clinic=vielle","/static/team.html?clinic=vielle"):
            self.assertEqual(self.request("GET",path)[0],403,path)
        status,raw=self.request("GET","/api/team/report?clinic=vielle",cookie=self.manager_cookie)
        self.assertEqual(status,200,raw);self.assertIn("totals",json.loads(raw))
        self.assertEqual(self.request("GET","/team.html?clinic=vielle",cookie=self.manager_cookie)[0],200)

    def test_identity_is_taken_from_session_and_mutations_need_same_origin(self):
        body={"client_id":uuid.uuid4().hex,"sequence":1,"activity_age":0,"user_id":self.manager}
        self.assertEqual(self.request("POST","/api/team/heartbeat?clinic=vielle",body,csrf=False)[0],403)
        self.assertEqual(self.request("POST","/api/team/heartbeat?clinic=vielle",body,extra={"Origin":"https://evil.test"})[0],403)
        status,raw=self.request("POST","/api/team/heartbeat?clinic=vielle",body)
        self.assertEqual(status,200,raw)
        status,raw=self.request("GET","/api/team/presence?clinic=vielle")
        self.assertEqual(next(m["status"] for m in json.loads(raw)["members"] if m["id"]==self.uid),"active")
        self.assertEqual(next(m["status"] for m in json.loads(raw)["members"] if m["id"]==self.manager),"offline")
        self.assertEqual(self.request("POST","/api/team/help?clinic=vielle",{**body,"recipient_id":self.other,"message":"Ajuda","request_id":uuid.uuid4().hex})[0],403)

    def test_missing_clinic_and_cross_clinic_are_rejected_and_anonymous_is_not_tracked(self):
        for path in ("/api/team/presence","/api/team/report"):
            self.assertEqual(self.request("GET",path)[0],400,path)
        self.assertEqual(self.request("GET","/api/team/presence?clinic=carla")[0],403)
        self.assertEqual(self.request("GET","/api/team/presence?clinic=vielle&clinic=carla")[0],400)
        self.assertEqual(self.request("GET","/api/team/presence?clinic=vielle",cookie="")[0],401)
        self.assertEqual(self.request("GET","/team.html?clinic=vielle",cookie="")[0],303)
        self.assertEqual(self.request("POST","/api/team/unknown?clinic=vielle",{})[0],405)
        for action in ("heartbeat","help"):
            self.assertEqual(self.request("POST",f"/api/team/{action}?clinic=vielle",[])[0],400)

    def test_logout_immediately_removes_online_status(self):
        self.request("POST","/api/team/heartbeat?clinic=vielle",{"client_id":uuid.uuid4().hex,"sequence":1,"activity_age":0})
        self.assertEqual(self.request("POST","/api/auth/logout")[0],200)
        status,raw=self.request("GET","/api/team/presence?clinic=vielle",cookie=self.manager_cookie)
        self.assertEqual(status,200,raw)
        self.assertEqual(next(m["status"] for m in json.loads(raw)["members"] if m["id"]==self.uid),"offline")


if __name__=="__main__":unittest.main()
