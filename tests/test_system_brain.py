import contextlib
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import importlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

from auth_store import AuthStore
import system_brain as brain


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "clinic.sqlite3"
        self.now = int(time.time())
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE sync_log(id INTEGER PRIMARY KEY,started_at INTEGER,finished_at INTEGER,ok INTEGER,message TEXT);
                CREATE TABLE clinica_sync_log(id INTEGER PRIMARY KEY,started_at INTEGER,finished_at INTEGER,ok INTEGER,message TEXT);
                CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE clinica_bookings(uuid TEXT,starts_at TEXT,registered_at TEXT,patient_name TEXT);
                CREATE TABLE clinica_sales(uuid TEXT,sale_date TEXT,total REAL,raw_json TEXT);
                CREATE TABLE quote_followup_contacts(patient_name TEXT);
            """)
            conn.execute("INSERT INTO sync_log VALUES(1,?,?,1,?)", (self.now-20,self.now-5,"secret_token=private PATIENT-NAME"))
            conn.execute("INSERT INTO clinica_sync_log VALUES(1,?,?,0,?)", (self.now-10,self.now-2,"PATIENT-NAME email@example.com api_key=private"))
            conn.execute("INSERT INTO app_settings VALUES('OPENAI_API_KEY','private-key')")
            conn.execute("INSERT INTO app_settings VALUES('PAID_TRAFFIC_LAST_SYNC',?)", (str(self.now-90000),))
            conn.execute("INSERT INTO clinica_bookings VALUES('private-id','2026-09-21',NULL,'PATIENT-NAME')")
            conn.execute("INSERT INTO clinica_sales VALUES('private-sale','2026-09-21',1234.5,'private_record')")
            conn.execute("INSERT INTO quote_followup_contacts VALUES('PATIENT-NAME')")
        self.app = {"BASE_DIR":Path(__file__).resolve().parents[1],"DATA_DIR":self.root,"DB_PATH":self.path,
                    "CONFIG_DEFAULTS":{},"configured":lambda v:bool(v)}
        brain._last_request.clear()

    def test_snapshot_is_read_only_and_contains_no_identifiers_or_secrets(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        snap=brain.snapshot(self.app,"vielle")
        text=json.dumps(snap)
        for forbidden in ("PATIENT-NAME","private-key","private_record","private-sale","email@example.com","secret_token"):
            self.assertNotIn(forbidden,text)
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse(brain.analysis_path(self.app).exists())
        self.assertEqual(snap["health"]["integrations"]["kommo"]["state"],"ok")
        self.assertEqual(snap["health"]["integrations"]["experts"]["state"],"error")
        self.assertEqual(snap["health"]["integrations"]["meta"]["state"],"stale")
        self.assertEqual(next(e for e in snap["evidence"] if e["id"]=="booking_dates")["count"],1)
        self.assertTrue(snap["ai"]["configured"])

    def test_missing_database_is_not_created(self):
        snap=brain.snapshot(self.app,"inspire")
        self.assertEqual(snap["health"]["database"],"missing")
        self.assertFalse(brain.clinic_path(self.app,"inspire").exists())
        self.assertIn("body_evolution",{n["id"] for n in snap["architecture"]["nodes"]})
        self.assertNotIn("patient_followup",{n["id"] for n in snap["architecture"]["nodes"]})

    def test_inventory_updates_and_new_module_is_flagged(self):
        base=self.root/"source";base.mkdir()
        (base/"first.py").write_text('def x(): return "/api/hello"')
        first=brain.inventory(base)
        (base/"second.py").write_text('from first import x\ndef y(): return x()')
        second=brain.inventory(base)
        self.assertNotEqual(first["fingerprint"],second["fingerprint"])
        self.assertEqual(second["endpoints"],["/api/hello"])
        self.assertEqual(next(f for f in second["files"] if f["name"]=="second.py")["dependencies"],["first.py"])
        with patch.dict(brain.MODULES,{"new_module":{"label":"Novo","view":"newView","actions":["view"]}}):
            graph=brain.architecture("vielle",second)
            new=next(n for n in graph["nodes"] if n["id"]=="new_module")
            self.assertFalse(new["mapped"])
            self.assertFalse(new["files_verified"])

    def test_incomplete_execution_is_not_reported_as_success(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO sync_log VALUES(2,?,NULL,0,'anything')",(self.now-8000,))
        self.assertEqual(brain.health(self.path,self.now)["integrations"]["kommo"]["state"],"unknown")

    def test_ai_sends_only_technical_facts_validates_evidence_and_saves_separately(self):
        snap=brain.snapshot(self.app,"vielle")
        result={"summary":"Diagnóstico técnico","findings":[{"title":"Verificar a sincronização","kind":"observacao","priority":"alta","detail":"A última tentativa falhou.","recommendation":"Verificar integração.","evidence_ids":["experts_sync"]}]}
        def response(request,timeout):
            body=json.loads(request.data)
            self.assertFalse(body["store"])
            self.assertEqual(body["max_completion_tokens"],2000)
            output = body["response_format"]
            self.assertEqual(output["type"], "json_schema")
            self.assertTrue(output["json_schema"]["strict"])
            schema = output["json_schema"]["schema"]
            self.assertFalse(schema["additionalProperties"])
            finding = schema["properties"]["findings"]["items"]
            self.assertEqual(set(finding["required"]), set(finding["properties"]))
            refs = finding["properties"]["evidence_ids"]
            self.assertEqual(refs["minItems"], 1)
            self.assertEqual(set(refs["items"]["enum"]), {e["id"] for e in snap["evidence"]})
            for text in ("PATIENT-NAME","private-key","email@example.com","private_record","private-sale"):
                self.assertNotIn(text,request.data.decode())
            self.assertEqual(request.get_header("Authorization"),"Bearer private-key")
            return io.BytesIO(json.dumps({"choices":[{"finish_reason":"stop","message":{"content":json.dumps(result)}}]}).encode())
        before=self.path.read_bytes()
        with patch("urllib.request.urlopen",side_effect=response):
            analysis=brain.ai_analysis(self.app,snap,"general")
        brain.save_analysis(self.app,"vielle",1,analysis)
        self.assertEqual(brain.latest_analysis(self.app,"vielle")["summary"],"Diagnóstico técnico")
        self.assertIsNone(brain.latest_analysis(self.app,"inspire"))
        self.assertEqual(before,self.path.read_bytes())
        result["findings"][0]["evidence_ids"]=["invented"]
        with patch("urllib.request.urlopen",side_effect=response), self.assertRaisesRegex(ValueError,"evidências"):
            brain.ai_analysis(self.app,snap,"general")

    def test_incomplete_or_refused_ai_output_is_not_misreported_as_missing_evidence(self):
        snap=brain.snapshot(self.app,"vielle")
        for reason, message, expected in (
            ("length", {"content": "{partial"}, "limite de tamanho"),
            ("stop", {"refusal": "private refusal text"}, "não concluiu"),
            ("content_filter", {"content": ""}, "não concluiu"),
        ):
            with self.subTest(reason=reason):
                response=io.BytesIO(json.dumps({"choices":[{"finish_reason":reason,"message":message}]}).encode())
                with patch("urllib.request.urlopen",return_value=response), self.assertRaisesRegex(ValueError,expected) as caught:
                    brain.ai_analysis(self.app,snap,"general")
                self.assertNotIn("private refusal", str(caught.exception))
                self.assertFalse(brain.analysis_path(self.app).exists())

    def test_provider_errors_are_redacted(self):
        snap=brain.snapshot(self.app,"vielle")
        error=urllib.error.HTTPError("https://api.openai.com",429,"quota",{},io.BytesIO(b"private-key PATIENT-NAME"))
        with patch("urllib.request.urlopen",side_effect=error), self.assertRaisesRegex(ValueError,"cota") as caught:
            brain.ai_analysis(self.app,snap,"general")
        self.assertNotIn("private-key",str(caught.exception))


class BrainHttpTests(BrainTests):
    def setUp(self):
        super().setUp()
        app=importlib.import_module("app")
        for name,value in (("DATA_DIR",self.root),("DB_PATH",self.path),("CONFIG_DEFAULTS",{})):
            patcher=patch.object(app,name,value);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(app.Handler,"log_message",return_value=None);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch("urllib.request.urlopen",side_effect=AssertionError("Unexpected external call"));patcher.start();self.addCleanup(patcher.stop)
        store=AuthStore(self.root/"auth.sqlite3");store.initialize()
        store.create_user("Master","master","Only-tests-password!",is_master=True)
        store.create_user("User","user","Only-tests-password!",clinic_keys=["vielle"])
        self.master="doc4docs_session="+store.login("master","Only-tests-password!")
        self.user="doc4docs_session="+store.login("user","Only-tests-password!")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler);self.server.auth_store=store
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path,method="GET",cookie=None,body=None,csrf=True):
        conn=http.client.HTTPConnection(*self.server.server_address,timeout=10)
        headers={"Cookie":cookie or self.master}
        if csrf:headers["X-DOC4DOCS-Request"]="1"
        if body is not None:headers["Content-Type"]="application/json";body=json.dumps(body)
        conn.request(method,path,body=body,headers=headers);response=conn.getresponse();raw=response.read();status=response.status;conn.close()
        return status,raw

    def test_master_guard_pages_api_and_csrf(self):
        for path in ("/master/brain.html","/static/master/brain.html","/master/brain.js","/api/master/brain?clinic=vielle"):
            self.assertEqual(self.request(path,cookie=self.user)[0],403,path)
            self.assertIn(self.request(path,cookie="anonymous=1")[0],(303,401),path)
            self.assertEqual(self.request(path)[0],200,path)
        self.assertEqual(self.request("/api/master/brain/analyze","POST",body={"focus":"general"},csrf=False)[0],403)
        self.assertEqual(self.request("/api/master/brain?clinic=invalid")[0],400)
        self.assertEqual(self.request("/api/master/brain?clinic=vielle&clinic=inspire")[0],400)
        self.assertEqual(self.request("/api/master/brain/analyze","POST",body={"focus":[]})[0],400)

    def test_analysis_throttle_and_success(self):
        with patch.object(brain,"ai_analysis",return_value={"summary":"Test","findings":[]}):
            self.assertEqual(self.request("/api/master/brain/analyze?clinic=vielle","POST",body={"focus":"general"})[0],200)
            self.assertEqual(self.request("/api/master/brain/analyze?clinic=vielle","POST",body={"focus":"general"})[0],429)
        result=json.loads(self.request("/api/master/brain?clinic=vielle")[1])
        self.assertEqual(result["analysis"]["summary"],"Test")


if __name__=="__main__":
    unittest.main()
