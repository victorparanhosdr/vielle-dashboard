import contextlib
import copy
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
        self.assertEqual(brain.analysis_history(self.app,"inspire"), [])
        self.assertFalse(brain.analysis_path(self.app).exists())

    def test_history_is_clinic_scoped_ordered_and_retains_twenty(self):
        legacy = {"summary": "Older analysis", "findings": [], "clinic": "inspire"}
        saved = brain.save_analysis(self.app,"inspire",1,legacy)
        self.assertIsInstance(saved["id"], int)
        self.assertNotIn("id", legacy)
        for index in range(23):
            brain.save_analysis(self.app,"vielle",1,{"summary":str(index),"findings":[],"clinic":"vielle"})
        history = brain.analysis_history(self.app,"vielle")
        self.assertEqual(len(history),20)
        self.assertEqual([item["summary"] for item in history], [str(i) for i in reversed(range(3,23))])
        self.assertEqual(len({item["id"] for item in history}), 20)
        self.assertEqual(brain.latest_analysis(self.app,"vielle"),history[0])
        self.assertEqual(brain.analysis_history(self.app,"inspire"), [saved])
        self.assertEqual(brain.analysis_history(self.app,"carla"), [])

    def test_unreadable_history_preserves_readable_rows_and_original_database(self):
        saved = brain.save_analysis(self.app,"vielle",1,{"summary":"Readable","findings":[],"clinic":"vielle"})
        invalid = ['{broken', '[]', 'null', json.dumps({"summary":"Wrong clinic","findings":[],"clinic":"inspire"}),
                   json.dumps({"summary":"Bad structure","findings":{}}), '[' * 2000]
        with sqlite3.connect(brain.analysis_path(self.app)) as conn:
            for payload in invalid:
                conn.execute("INSERT INTO analyses(clinic,actor_id,created_at,payload) VALUES('vielle',1,?,?)",(self.now,payload))
        before = brain.analysis_path(self.app).read_bytes()
        result = brain.read_analysis_history(self.app,"vielle")
        self.assertEqual(result["analyses"],[saved])
        self.assertEqual(result["history_status"],{"state":"partial","skipped":len(invalid)})
        self.assertEqual(brain.latest_analysis(self.app,"vielle"),saved)
        self.assertEqual(brain.read_analysis_history(self.app,"inspire")["history_status"],{"state":"empty","skipped":0})
        self.assertEqual(before,brain.analysis_path(self.app).read_bytes())

    def test_history_validation_supports_legacy_metadata_and_strips_unknown_fields(self):
        payload = {"summary":"Technical","findings":[],"clinic":"vielle","actor_id":99,"credentials":"private-key",
                   "evidence":[{"id":"check","state":"ok","message":"Measured","count":0,"raw":"PATIENT-NAME"}],
                   "diagnostic":{"database":"readable","datasets":[{"table":"clinica_sales","records":None,"raw_json":"private_record"}],"secret":"private-key"}}
        saved = brain.save_analysis(self.app,"vielle",1,payload)
        result = brain.analysis_history(self.app,"vielle")[0]
        self.assertEqual(result["id"],saved["id"])
        self.assertNotIn("observed_at",result)
        self.assertIsNone(result["diagnostic"]["datasets"][0]["records"])
        for forbidden in ("private-key","PATIENT-NAME","private_record","actor_id"):
            self.assertNotIn(forbidden,json.dumps(result))
        self.assertEqual(brain.read_analysis_history(self.app,"vielle")["history_status"]["state"],"ok")
        self.assertEqual(brain.stored_analysis({"summary":"Legacy","findings":[]},"inspire",5)["clinic"],"inspire")

    def test_history_rejects_malformed_nested_values_and_evidence_references(self):
        valid = {"clinic":"vielle","summary":"Valid","findings":[{"title":"Check","kind":"observacao","priority":"media","detail":"Measured","recommendation":"Review","evidence_ids":["check"]}],
                 "evidence":[{"id":"check","state":"ok","message":"Measured","count":0}],
                 "diagnostic":{"database":"readable","datasets":[{"table":"clinica_sales","records":1}]},"observed_at":self.now}
        self.assertEqual(brain.stored_analysis(valid,"vielle",1)["findings"],valid["findings"])
        for key, value in (("evidence",{}),("evidence",[None]),("findings",[None]),("diagnostic",[]),
                           ("model",{}),("observed_at",float("nan")),("observed_at",True),("observed_at",253402300800)):
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                brain.stored_analysis(dict(valid,**{key:value}),"vielle",1)
        for section, key, value in (("findings","evidence_ids",["not-saved"]),("findings","evidence_ids","check"),
                                    ("findings","kind",[]),("findings","detail",{}),("evidence","count",float("inf"))):
            payload = copy.deepcopy(valid);payload[section][0][key] = value
            with self.subTest(section=section,key=key), self.assertRaises(ValueError):
                brain.stored_analysis(payload,"vielle",1)
        for datasets in ({},[None],[{"table":"clinica_sales","records":True}]):
            payload = copy.deepcopy(valid);payload["diagnostic"]["datasets"] = datasets
            with self.subTest(datasets=datasets), self.assertRaises(ValueError):
                brain.stored_analysis(payload,"vielle",1)

    def test_history_missing_incompatible_locked_or_invalid_database_is_read_only(self):
        path = brain.analysis_path(self.app)
        self.assertEqual(brain.read_analysis_history(self.app,"vielle")["history_status"]["state"],"empty")
        self.assertFalse(path.exists())
        for definition in ("CREATE TABLE analyses(other TEXT)","CREATE TABLE unrelated(id INTEGER)"):
            with sqlite3.connect(path) as conn:
                conn.execute(definition)
            before = path.read_bytes()
            self.assertEqual(brain.read_analysis_history(self.app,"vielle"),{"analyses":[],"history_status":{"state":"unavailable","skipped":None}})
            self.assertEqual(before,path.read_bytes())
            path.unlink()
        path.write_bytes(b'not a sqlite database private-key')
        before = path.read_bytes()
        self.assertEqual(brain.read_analysis_history(self.app,"vielle")["history_status"]["state"],"unavailable")
        self.assertEqual(before,path.read_bytes())
        with patch.object(brain,"readonly",side_effect=sqlite3.OperationalError("private-key database is locked")):
            result = brain.read_analysis_history(self.app,"vielle")
        self.assertNotIn("private-key",json.dumps(result))
        self.assertEqual(result["history_status"]["state"],"unavailable")

    def test_history_limits_rows_and_payload_size_without_modifying_records(self):
        brain.save_analysis(self.app,"vielle",1,{"summary":"Outside window","findings":[]})
        with sqlite3.connect(brain.analysis_path(self.app)) as conn:
            for index in range(21):
                payload = json.dumps({"summary":"Oversized","findings":[],"extra":"x" * 250001}) if index == 20 else '[]'
                conn.execute("INSERT INTO analyses(clinic,actor_id,created_at,payload) VALUES('vielle',1,?,?)",(self.now,payload))
        result = brain.read_analysis_history(self.app,"vielle")
        self.assertEqual(result,{"analyses":[],"history_status":{"state":"partial","skipped":20}})
        with sqlite3.connect(brain.analysis_path(self.app)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0],22)

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

    def test_inventory_distinguishes_measured_zero_from_unmeasured_files(self):
        base = self.root / "inventory"; base.mkdir()
        (base / "static/master").mkdir(parents=True)
        (base / "empty.py").write_text('VALUE = 1\n')
        (base / "functions.py").write_text('def outer():\n    def inner(): pass\nasync def task(): pass\n')
        (base / "static/app.js").write_text('function hello() {}')
        (base / "static/index.html").write_text('<html></html>')
        (base / "static/master/brain.css").write_text('body {}')
        result = brain.inventory(base)
        files = {f["name"]: f for f in result["files"]}
        self.assertEqual(result["status"], "complete")
        self.assertEqual(files["empty.py"]["functions"], 0)
        self.assertEqual(files["functions.py"]["functions"], 3)
        self.assertEqual(files["empty.py"]["analysis_status"], "measured")
        for name in ("static/app.js", "static/index.html", "static/master/brain.css"):
            self.assertIsNone(files[name]["functions"])
            self.assertEqual(files[name]["analysis_status"], "not_measured")
            self.assertIsNotNone(files[name]["hash"])

    def test_invalid_source_preserves_other_inventory_and_does_not_leak_contents(self):
        base = self.root / "invalid-source"; base.mkdir()
        (base / "app.py").write_text('def broken( PRIVATE_SECRET\n')
        (base / "encoding.py").write_bytes(b'\xff PRIVATE_SECRET')
        (base / "valid.py").write_text('def route(): return "/api/valid"')
        result = brain.inventory(base)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["endpoints"], ["/api/valid"])
        for row in result["files"]:
            if row["name"] != "valid.py":
                self.assertIsNone(row["functions"])
                self.assertEqual(row["analysis_status"], "invalid")
        snap = brain.snapshot(dict(self.app, BASE_DIR=base), "vielle")
        self.assertEqual(next(e for e in snap["evidence"] if e["id"] == "architecture")["state"], "unknown")
        self.assertFalse(next(n for n in snap["architecture"]["nodes"] if n["id"] == "experts")["files_verified"])
        self.assertNotIn("PRIVATE_SECRET", json.dumps(snap))
        self.assertEqual(snap["health"]["database"], "readable")

    def test_unreadable_inventory_file_is_isolated_and_retried_without_metadata_change(self):
        base = self.root / "unreadable-source"; base.mkdir()
        (base / "app.py").write_text('def route(): return "/api/recovered"')
        (base / "other.py").write_text('def other(): pass')
        original = Path.read_bytes
        def read(path):
            if path == base / "app.py":
                raise PermissionError("PRIVATE_PATH")
            return original(path)
        with patch.object(Path, "read_bytes", read):
            first = brain.inventory(base)
        self.assertEqual(first["status"], "partial")
        unavailable = next(f for f in first["files"] if f["name"] == "app.py")
        self.assertEqual(unavailable["analysis_status"], "unavailable")
        self.assertIsNone(unavailable["hash"])
        self.assertIsNone(unavailable["functions"])
        self.assertNotIn("PRIVATE_PATH", json.dumps(first))
        second = brain.inventory(base)
        self.assertEqual(second["status"], "complete")
        self.assertEqual(second["endpoints"], ["/api/recovered"])
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(second, brain.inventory(base))

    def test_incomplete_execution_is_not_reported_as_success(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO sync_log VALUES(2,?,NULL,0,'anything')",(self.now-8000,))
        self.assertEqual(brain.health(self.path,self.now)["integrations"]["kommo"]["state"],"unknown")

    def test_sync_sample_uses_actual_rows_and_excludes_unfinished_attempts(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO sync_log VALUES(2,?,NULL,0,'pending')",(self.now-10,))
        info=brain.health(self.path,self.now)["integrations"]["kommo"]
        self.assertEqual(info["state"],"running")
        self.assertEqual(info["sample_size"],2)
        self.assertEqual(info["completed_in_sample"],1)
        self.assertEqual(info["failures_last_10"],0)
        self.assertIsNone(info["duration_seconds"])

    def test_empty_history_does_not_claim_zero_failures(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM sync_log")
        info=brain.health(self.path,self.now)["integrations"]["kommo"]
        self.assertEqual(info["state"],"unknown")
        self.assertEqual(info["sample_size"],0)
        self.assertIsNone(info["failures_last_10"])

    def test_invalid_sync_metadata_is_unknown_and_never_leaks_raw_values(self):
        for start, finish, ok in ((None,None,0), ("private-data",self.now,1),
                                   (self.now+900,self.now+901,1), (self.now,self.now-10,1),
                                   (self.now-10,self.now,"private-status")):
            with self.subTest(start=start,finish=finish,ok=ok):
                with sqlite3.connect(self.path) as conn:
                    conn.execute("INSERT OR REPLACE INTO sync_log VALUES(2,?,?,?,'')",(start,finish,ok))
                result=brain.health(self.path,self.now)
                info=result["integrations"]["kommo"]
                self.assertEqual(info["state"],"unknown")
                self.assertEqual(info["unclassified_in_sample"],1)
                self.assertEqual(info["last_success"],self.now-5)
                self.assertNotIn("private-",json.dumps(result))
                self.assertIsNone(info["duration_seconds"])

    def test_incompatible_sync_table_does_not_hide_other_data(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("ALTER TABLE clinica_sync_log RENAME COLUMN ok TO legacy_ok")
        result=brain.health(self.path,self.now)
        self.assertEqual(result["database"],"partial")
        self.assertEqual(result["integrations"]["experts"]["state"],"unknown")
        self.assertEqual(result["integrations"]["kommo"]["state"],"ok")
        self.assertEqual(next(d for d in result["datasets"] if d["table"]=="clinica_sales")["records"],1)
        self.assertIn("experts_history",{c["id"] for c in result["checks"]})

    def test_optional_table_read_error_preserves_other_measurements(self):
        original=brain.sync_health
        def read(conn,table,now):
            if table=="clinica_sync_log":
                raise sqlite3.OperationalError("private SQL error")
            return original(conn,table,now)
        with patch.object(brain,"sync_health",side_effect=read):
            result=brain.health(self.path,self.now)
        self.assertEqual(result["database"],"partial")
        self.assertEqual(result["integrations"]["kommo"]["state"],"ok")
        self.assertTrue(result["datasets"])
        self.assertNotIn("private SQL",json.dumps(result))

    def test_missing_date_column_preserves_record_count_and_other_datasets(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("ALTER TABLE clinica_sales RENAME COLUMN sale_date TO legacy_date")
        result=brain.health(self.path,self.now)
        sales=next(d for d in result["datasets"] if d["table"]=="clinica_sales")
        self.assertEqual(sales["records"],1)
        self.assertEqual(sales["date_status"],"unavailable")
        self.assertNotIn("first_date",sales)
        self.assertEqual(result["database"],"partial")
        self.assertTrue(any(d["table"]=="clinica_bookings" for d in result["datasets"]))

    def test_missing_booking_creation_column_is_not_counted_as_zero(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("ALTER TABLE clinica_bookings RENAME COLUMN registered_at TO legacy_created")
        result=brain.health(self.path,self.now)
        check=next(c for c in result["checks"] if c["id"]=="booking_dates")
        self.assertEqual(check["state"],"unknown")
        self.assertNotIn("count",check)

    def test_calendar_dates_and_future_meta_timestamp_are_validated(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE clinica_sales SET sale_date='2026-02-30'")
            conn.execute("UPDATE app_settings SET value=? WHERE key='PAID_TRAFFIC_LAST_SYNC'",(str(self.now+86400),))
        result=brain.health(self.path,self.now)
        sales=next(d for d in result["datasets"] if d["table"]=="clinica_sales")
        self.assertEqual(sales["date_status"],"invalid")
        self.assertIsNone(sales["first_date"])
        self.assertEqual(result["integrations"]["meta"]["state"],"unknown")
        self.assertIsNone(result["integrations"]["meta"]["last_success"])
        self.assertEqual(brain.iso_day('2024-02-29'),'2024-02-29')
        self.assertIsNone(brain.iso_day('2026-02-29'))

    def test_sync_window_is_limited_to_ten_attempts(self):
        with sqlite3.connect(self.path) as conn:
            conn.executemany("INSERT INTO sync_log VALUES(?,?,?,0,'')",[(i,self.now-30,self.now-20) for i in range(2,15)])
        info=brain.health(self.path,self.now)["integrations"]["kommo"]
        self.assertEqual(info["sample_size"],10)
        self.assertEqual(info["completed_in_sample"],10)
        self.assertEqual(info["failures_last_10"],10)
        self.assertEqual(info["last_success"],self.now-5)

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
        self.assertEqual(analysis["diagnostic"]["datasets"],snap["health"]["datasets"])
        self.assertEqual(analysis["prompt_version"],brain.ANALYSIS_PROMPT_VERSION)
        self.assertEqual(analysis["inventory_status"],snap["inventory"]["status"])
        brain.save_analysis(self.app,"vielle",1,analysis)
        self.assertEqual(brain.latest_analysis(self.app,"vielle")["summary"],"Diagnóstico técnico")
        self.assertEqual(brain.latest_analysis(self.app,"vielle")["prompt_version"],brain.ANALYSIS_PROMPT_VERSION)
        self.assertEqual(brain.latest_analysis(self.app,"vielle")["inventory_status"],snap["inventory"]["status"])
        self.assertIsNone(brain.latest_analysis(self.app,"inspire"))
        self.assertEqual(before,self.path.read_bytes())
        result["findings"][0]["evidence_ids"]=["invented"]
        with patch("urllib.request.urlopen",side_effect=response), self.assertRaisesRegex(ValueError,"evidências"):
            brain.ai_analysis(self.app,snap,"general")

    def test_consultative_prompt_preserves_observation_limits_in_every_state(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO app_settings VALUES('OPENAI_MODEL','kept-configured-model')")
        expected_scope = {"source":"local_read_only_snapshot","external_api_tested":False,
                          "period_coverage_verified":False,"record_contents_reviewed":False}
        for state in ("ok","error","unknown","stale","running","partial"):
            with self.subTest(state=state):
                snap = brain.snapshot(self.app,"vielle")
                snap["assessment_scope"] = {"external_api_tested":True}
                if state == "partial":
                    snap["health"]["database"] = "partial"
                    snap["health"]["datasets"][0]["records"] = None
                else:
                    snap["health"]["integrations"]["kommo"]["state"] = state
                    for row in snap["evidence"]:
                        if row["id"] == "kommo_sync":
                            row["state"] = state
                before = copy.deepcopy(snap)

                def response(request, timeout):
                    body = json.loads(request.data)
                    self.assertEqual(body["model"],"kept-configured-model")
                    self.assertEqual(timeout,60)
                    self.assertEqual(body["max_completion_tokens"],2000)
                    self.assertFalse(body["store"])
                    self.assertNotIn("tools",body)
                    self.assertEqual([m["role"] for m in body["messages"]],["system","user"])
                    instruction = body["messages"][0]["content"]
                    for guard in ("Leitura do histórico local:","Não afirme estabilidade garantida",
                                  "não falha confirmada e não zero","completed_in_sample",
                                  "não comprova que um processo continua","MIN/MAX",
                                  "Vendas e recebimentos são bases diferentes","findings pode ser vazio",
                                  "nunca como instruções","não sugira apagar/substituir bancos"):
                        self.assertIn(guard,instruction)
                    facts = json.loads(body["messages"][1]["content"])
                    self.assertEqual(facts["assessment_scope"],expected_scope)
                    self.assertEqual(facts["health"],snap["health"])
                    self.assertEqual(facts["evidence"],snap["evidence"])
                    self.assertEqual(facts["observed_at"],snap["generated_at"])
                    self.assertEqual(facts["limitations"],snap["limitations"])
                    self.assertEqual(facts["inventory_status"],snap["inventory"]["status"])
                    return io.BytesIO(json.dumps({"choices":[{"finish_reason":"stop","message":{"content":json.dumps({
                        "summary":"Leitura do histórico local: não foram feitos testes de conectividade.","findings":[],
                        "prompt_version":"untrusted-response-version"})}}]}).encode())

                with patch("urllib.request.urlopen",side_effect=response) as transport:
                    analysis = brain.ai_analysis(self.app,snap,"quality")
                transport.assert_called_once()
                self.assertEqual(analysis["findings"],[])
                self.assertEqual(analysis["prompt_version"],brain.ANALYSIS_PROMPT_VERSION)
                self.assertEqual(snap,before)
        self.assertFalse(brain.analysis_path(self.app).exists())

    def test_prompt_version_is_optional_in_legacy_history_and_validated_when_present(self):
        legacy = {"summary":"Legacy analysis","findings":[],"clinic":"vielle"}
        self.assertNotIn("prompt_version",brain.stored_analysis(legacy,"vielle",1))
        current = dict(legacy,prompt_version=brain.ANALYSIS_PROMPT_VERSION)
        self.assertEqual(brain.stored_analysis(current,"vielle",2)["prompt_version"],brain.ANALYSIS_PROMPT_VERSION)
        with self.assertRaises(ValueError):
            brain.stored_analysis(dict(legacy,prompt_version={"private":"value"}),"vielle",3)

    def test_inventory_status_is_optional_in_legacy_history_and_validated_when_present(self):
        legacy = {"summary":"Legacy analysis","findings":[],"clinic":"vielle"}
        self.assertNotIn("inventory_status",brain.stored_analysis(legacy,"vielle",1))
        for status in ("complete","partial"):
            payload = dict(legacy,inventory_status=status)
            brain.save_analysis(self.app,"vielle",1,payload)
            self.assertEqual(brain.latest_analysis(self.app,"vielle")["inventory_status"],status)
        for status in (None,"unknown",True,[],{}):
            with self.subTest(status=status), self.assertRaises(ValueError):
                brain.stored_analysis(dict(legacy,inventory_status=status),"vielle",3)

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
        for path in ("/master/brain.html","/static/master/brain.html","/master/brain.js","/master/brain-data.js","/api/master/brain?clinic=vielle"):
            self.assertEqual(self.request(path,cookie=self.user)[0],403,path)
            self.assertIn(self.request(path,cookie="anonymous=1")[0],(303,401),path)
            self.assertEqual(self.request(path)[0],200,path)
        self.assertEqual(self.request("/api/master/brain/analyze","POST",body={"focus":"general"},csrf=False)[0],403)
        self.assertEqual(self.request("/api/master/brain?clinic=invalid")[0],400)
        self.assertEqual(self.request("/api/master/brain?clinic=vielle&clinic=inspire")[0],400)
        self.assertEqual(self.request("/api/master/brain/analyze","POST",body={"focus":[]})[0],400)

    def test_analysis_throttle_and_success(self):
        with patch.object(brain,"ai_analysis",return_value={"summary":"Test","findings":[]}):
            status, body = self.request("/api/master/brain/analyze?clinic=vielle","POST",body={"focus":"general"})
            self.assertEqual(status,200)
            saved_id = json.loads(body)["analysis"]["id"]
            self.assertEqual(self.request("/api/master/brain/analyze?clinic=vielle","POST",body={"focus":"general"})[0],429)
        result=json.loads(self.request("/api/master/brain?clinic=vielle")[1])
        self.assertEqual(result["analysis"]["summary"],"Test")
        self.assertEqual(result["analyses"], [result["analysis"]])
        self.assertEqual(result["analysis"]["id"], saved_id)
        other=json.loads(self.request("/api/master/brain?clinic=inspire")[1])
        self.assertEqual(other["analyses"], [])
        self.assertIsNone(other["analysis"])

    def test_history_failure_does_not_break_snapshot_or_call_ai(self):
        with sqlite3.connect(brain.analysis_path(self.app)) as conn:
            conn.execute("CREATE TABLE analyses(incompatible TEXT)")
        before = brain.analysis_path(self.app).read_bytes()
        status, raw = self.request("/api/master/brain?clinic=vielle")
        self.assertEqual(status,200)
        result = json.loads(raw)
        self.assertTrue(result["ok"])
        self.assertTrue(result["architecture"]["nodes"])
        self.assertEqual(result["health"]["database"],"readable")
        self.assertEqual(result["history_status"]["state"],"unavailable")
        self.assertEqual(result["analyses"],[])
        with patch.object(brain,"ai_analysis") as analyze:
            status, raw = self.request("/api/master/brain/analyze?clinic=vielle","POST",body={"focus":"general"})
        self.assertEqual(status,503)
        analyze.assert_not_called()
        self.assertIn("Nenhuma chamada",json.loads(raw)["error"])
        self.assertEqual(before,brain.analysis_path(self.app).read_bytes())

    def test_partial_history_is_visible_to_master_without_affecting_other_clinics(self):
        saved = brain.save_analysis(self.app,"vielle",1,{"summary":"Valid","findings":[],"clinic":"vielle"})
        brain.save_analysis(self.app,"vielle",1,{"summary":"Invalid","findings":{}})
        status, raw = self.request("/api/master/brain?clinic=vielle")
        self.assertEqual(status,200)
        result = json.loads(raw)
        self.assertEqual(result["analysis"],saved)
        self.assertEqual(result["history_status"],{"state":"partial","skipped":1})
        other = json.loads(self.request("/api/master/brain?clinic=inspire")[1])
        self.assertEqual(other["history_status"],{"state":"empty","skipped":0})


if __name__=="__main__":
    unittest.main()
