import base64
from contextlib import contextmanager
import http.client
from http.server import ThreadingHTTPServer
import importlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from auth_store import AuthStore
from auth_http import LoginLimiter
import body_evolution as body
from access_policy import clinic_permissions


def sample_exam(source="handymet"):
    from reportlab.pdfgen.canvas import Canvas
    stream = io.BytesIO()
    canvas = Canvas(stream, pagesize=(600, 800))
    canvas.setFont("Helvetica", 10)
    if source == "handymet":
        for index, text in enumerate([
            "HandyMet handymet.com Calorimetria", "Paciente Ficticio",
            "01/02/2026 09:42", "168 cm 24.8", "70 kg",
            "Taxa Metabólica Basal 1500 KCal/Dia", "TMB Previsto: 1400",
            "Gasto energético total 2400 KCal/Dia",
            "0.81 63.1% 36.9% 5.08 ml/ Kg.min 9.69 l/min",
        ]):
            canvas.drawString(30, 750 - index * 30, text)
    else:
        canvas.drawString(30, 770, "InBody120 (Paciente Ficticio) 168 cm 01.02.2026. 09:32")
        for x, top, text in [(.44,.14,"35.1"),(.44,.208,"13.6"),(.44,.23,"61.5"),
                             (.76,.30,"26.4"),(.76,.318,"47.9"),(.76,.334,"1405"),
                             (.76,.35,"0.88"),(.76,.366,"6"),(.30,.475,"21.8"),(.30,.503,"22.1")]:
            canvas.drawString(x * 600, 800 - top * 800 - 8, text)
    canvas.save()
    return stream.getvalue()


def setup_database(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE IF NOT EXISTS clinica_patients(uuid TEXT PRIMARY KEY, name TEXT, phone TEXT)")
    conn.execute("INSERT OR IGNORE INTO clinica_patients VALUES ('experts-1', 'Paciente Teste', '11999990000')")
    body.initialize(conn)
    return conn


class BodyStoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = setup_database(":memory:")
        self.addCleanup(self.conn.close)
        self.actor = {"id": 1, "nome": "Profissional Teste"}
        self.patient = body.enroll(self.conn, "experts-1", self.actor)
        self.conn.commit()
        self.payload = {"patient_id": self.patient, "exam_at": "2026-01-01T10:00", "source": "manual",
                        "method": "Balança", "professional": "Dra. Teste", "fields": {"weight_kg": 70}, "confirmed": True}

    def test_enroll_is_idempotent_and_uses_experts_id(self):
        self.assertEqual(body.enroll(self.conn, "experts-1", self.actor), self.patient)
        with self.assertRaises(ValueError): body.enroll(self.conn, "other-clinic", self.actor)
        self.assertEqual(body.search_patients(self.conn, "Pa")[0]["enrolled_id"], self.patient)
        self.assertEqual(body.search_patients(self.conn, "%"), [])

    def test_dates_order_independent_of_import_time_and_missing_is_not_zero(self):
        body.save_evaluation(self.conn, {**self.payload, "exam_at": "2026-02-01T10:00"}, self.actor)
        body.save_evaluation(self.conn, self.payload, self.actor)
        result = body.patient_detail(self.conn, self.patient)["evaluations"]
        self.assertEqual([row["exam_at"] for row in result], ["2026-01-01T10:00", "2026-02-01T10:00"])
        self.assertNotIn("fat_pct", result[0]["fields"])

    def test_validation_and_confirmation(self):
        for change in [{"fields": {"weight_kg": -1}}, {"fields": {"fat_pct": 101}}, {"fields": {"weight_kg": float("nan")}},
                       {"fields": {}}, {"confirmed": False}, {"exam_at": "2099-01-01T00:00"},
                       {"source": "inbody"}, {"exam_at": "not-a-date"}, {"fields": {"evil": 1}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                body.save_evaluation(self.conn, {**self.payload, **change}, self.actor)

    def test_revisions_and_concurrent_edits(self):
        record = body.save_evaluation(self.conn, self.payload, self.actor)
        body.save_evaluation(self.conn, {**self.payload, "id": record, "version": 1, "fields": {"weight_kg": 71}}, self.actor)
        self.assertEqual(body.patient_detail(self.conn, self.patient)["evaluations"][0]["version"], 2)
        with self.assertRaises(ValueError):
            body.save_evaluation(self.conn, {**self.payload, "id": record, "version": 1}, self.actor)
        snapshots = [json.loads(row[0]) for row in self.conn.execute("SELECT snapshot_json FROM body_revisions ORDER BY id")]
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(json.loads(snapshots[1]["before"]["fields_json"])["weight_kg"], 70)

    def test_pdf_duplicate_rollback_and_no_patient_sync_dependency(self):
        attachment = (b"%PDF-test-original", {"source": "inbody", "fields": {"weight_kg": 70}})
        with self.conn:
            body.save_evaluation(self.conn, {**self.payload, "source": "inbody"}, self.actor, attachment)
        with self.assertRaises(ValueError), self.conn:
            body.save_evaluation(self.conn, {**self.payload, "source": "inbody"}, self.actor, attachment)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM body_documents").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM body_evaluations").fetchone()[0], 1)
        self.conn.execute("DELETE FROM clinica_patients")
        self.assertEqual(body.patient_detail(self.conn,self.patient)["patient"]["display_name"], "Paciente Teste")

    def test_upload_rejects_invalid_base64_and_size(self):
        for value in [None, "!!!", base64.b64encode(b"hello").decode(), "x"*(body.MAX_PDF*2)]:
            with self.assertRaises(ValueError):body.decode_pdf(value)


class BodyHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AuthStore(Path(self.temp.name)/"auth.db")
        self.store.initialize()
        for login, clinics, perms in [
            ("viewer", ["inspire"], {"inspire":["body_evolution.view"]}),
            ("writer", ["inspire"], {"inspire":clinic_permissions("inspire")}),
            ("creator", ["inspire"], {"inspire":["body_evolution.view", "body_evolution.create"]}),
            ("editor", ["inspire"], {"inspire":["body_evolution.view", "body_evolution.edit"]}),
            ("outsider", ["vielle"], {"vielle":["dashboard.view"]}),
            ("denied", ["inspire"], {"inspire":["dashboard.view"]})]:
            self.store.create_user(login, login, "Test-password-strong!", clinic_keys=clinics, permissions=perms)
        self.app = importlib.import_module("app")
        database = Path(self.temp.name)/"clinic.db"
        setup_database(database).close()
        @contextmanager
        def connect():
            conn=sqlite3.connect(database);conn.row_factory=sqlite3.Row;conn.execute("PRAGMA foreign_keys=ON")
            try:
                with conn:yield conn
            finally:conn.close()
        from contextlib import nullcontext
        for target, name, value in [(self.app,"db",connect),(self.app,"clinic_context",lambda _:nullcontext()),(self.app.Handler,"log_message",lambda *args:None)]:
            p=patch.object(target,name,value);p.start();self.addCleanup(p.stop)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),self.app.Handler)
        self.server.auth_store=self.store;self.server.login_limiter=LoginLimiter()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,method,path,payload=None,login=None,csrf=True):
        headers={}
        if login:
            token=self.store.login(login,"Test-password-strong!")
            headers["Cookie"]="doc4docs_session="+token
        if csrf:headers["X-DOC4DOCS-Request"]="1"
        data=None
        if payload is not None:headers["Content-Type"]="application/json";data=json.dumps(payload)
        conn=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        conn.request(method,path,data,headers);r=conn.getresponse();result=(r.status,dict(r.getheaders()),r.read());conn.close();return result

    def test_login_deeplink_and_permission_guards(self):
        route="/body-evolution.html?clinic=inspire&patient=abc"
        response=self.request("GET",route)
        self.assertEqual(response[0],303);self.assertIn("next=",response[1]["Location"]);self.assertIn("patient%3Dabc",response[1]["Location"])
        for login,status in [(None,401),("outsider",403),("denied",403),("viewer",200)]:
            self.assertEqual(self.request("GET","/api/body/patients?clinic=inspire",login=login)[0],status)
        for route in ["/body-evolution.html?clinic=inspire","/static/body-evolution.html?clinic=inspire","/static/../body-evolution.html?clinic=inspire"]:
            self.assertEqual(self.request("GET",route,login="denied")[0],403)
        self.assertEqual(self.request("GET","/api/body/patients?clinic=vielle",login="outsider")[0],403)
        self.assertEqual(self.request("POST","/api/body/enroll?clinic=inspire",{"experts_uuid":"experts-1"},"viewer")[0],403)
        self.assertEqual(self.request("POST","/api/body/enroll?clinic=inspire",{"experts_uuid":"experts-1"},"writer",csrf=False)[0],403)
        self.assertEqual(self.request("GET","/api/body/document?clinic=inspire&id=x",login="viewer")[0],403)

    def test_http_create_read_edit_and_audit(self):
        result=self.request("POST","/api/body/enroll?clinic=inspire",{"experts_uuid":"experts-1"},"writer")
        self.assertEqual(result[0],200,result[2]);patient=json.loads(result[2])["id"]
        data={"patient_id":patient,"exam_at":"2026-01-01T10:00","source":"manual","method":"Fita","professional":"Dra Teste","fields":{"waist_cm":88},"confirmed":True}
        self.assertEqual(self.request("POST","/api/body/evaluation?clinic=inspire",data,"viewer")[0],403)
        result=self.request("POST","/api/body/evaluation?clinic=inspire",data,"writer")
        self.assertEqual(result[0],200,result[2]);record=json.loads(result[2])["id"]
        read=json.loads(self.request("GET",f"/api/body/patient?clinic=inspire&id={patient}",login="viewer")[2])
        self.assertEqual(read["evaluations"][0]["fields"]["waist_cm"],88)
        data.update(id=record,version=1,fields={"waist_cm":87})
        self.assertEqual(self.request("POST","/api/body/evaluation?clinic=inspire",data,"writer")[0],200)
        self.assertEqual(self.request("POST","/api/body/evaluation?clinic=inspire",data,"writer")[0],400)
        read=json.loads(self.request("GET",f"/api/body/revisions?clinic=inspire&patient={patient}&id={record}",login="viewer")[2])
        self.assertEqual(len(read["revisions"]),2)

    def test_pdf_preview_save_download_and_duplicate(self):
        pdf = sample_exam()
        encoded = base64.b64encode(pdf).decode()
        route = "/api/body/import?clinic=inspire"
        self.assertEqual(self.request("POST",route,{"pdf":encoded},"viewer")[0],403)
        result = self.request("POST",route,{"pdf":encoded},"writer")
        self.assertEqual(result[0],200,result[2])
        exam = json.loads(result[2])["exam"]
        patient = json.loads(self.request("POST","/api/body/enroll?clinic=inspire",{"experts_uuid":"experts-1"},"writer")[2])["id"]
        detail_route = f"/api/body/patient?clinic=inspire&id={patient}"
        self.assertEqual(json.loads(self.request("GET",detail_route,login="viewer")[2])["evaluations"],[])
        payload = {**exam,"patient_id":patient,"pdf":encoded,"professional":"Dra Teste","confirmed":True}
        result = self.request("POST","/api/body/evaluation?clinic=inspire",payload,"writer")
        self.assertEqual(result[0],200,result[2])
        row = json.loads(self.request("GET",detail_route,login="viewer")[2])["evaluations"][0]
        route = f"/api/body/document?clinic=inspire&patient={patient}&id={row['document_id']}"
        self.assertEqual(self.request("GET",route,login="viewer")[0],403)
        download = self.request("GET",route,login="writer")
        self.assertEqual(download[0],200);self.assertEqual(download[2],pdf)
        self.assertIn("attachment",download[1]["Content-Disposition"])
        self.assertEqual(self.request("GET",route.replace(patient,"different-patient"),login="writer")[0],404)
        self.assertEqual(self.request("POST","/api/body/evaluation?clinic=inspire",payload,"writer")[0],400)
        self.assertEqual(len(json.loads(self.request("GET",detail_route,login="viewer")[2])["evaluations"]),1)

    def test_create_edit_permissions_and_profile_for_inspire(self):
        self.store.save_profile("Endocrinologia",["body_evolution.view","body_evolution.create"])
        self.assertNotIn("body_evolution.view",clinic_permissions("vielle"))
        self.assertNotIn("body_evolution.view",clinic_permissions("carla"))
        patient = json.loads(self.request("POST","/api/body/enroll?clinic=inspire",{"experts_uuid":"experts-1"},"creator")[2])["id"]
        payload = {"patient_id":patient,"exam_at":"2026-02-01T09:00","source":"manual","method":"Balança",
                   "professional":"Dra Teste","fields":{"weight_kg":70},"confirmed":True}
        route = "/api/body/evaluation?clinic=inspire"
        self.assertEqual(self.request("POST",route,payload,"editor")[0],403)
        record = json.loads(self.request("POST",route,payload,"creator")[2])["id"]
        payload.update(id=record,version=1,fields={"weight_kg":71})
        self.assertEqual(self.request("POST",route,payload,"creator")[0],403)
        self.assertEqual(self.request("POST",route,payload,"editor")[0],200)


class ExamReaderTests(unittest.TestCase):
    def test_supported_layouts_keep_dates_and_different_metabolic_measures(self):
        from body_exams import parse_pdf
        inbody = parse_pdf(sample_exam("inbody"))
        handymet = parse_pdf(sample_exam("handymet"))
        self.assertEqual(inbody["exam_at"],"2026-02-01T09:32")
        self.assertEqual(handymet["exam_at"],"2026-02-01T09:42")
        self.assertEqual(inbody["fields"]["muscle_kg"],26.4)
        self.assertEqual(inbody["fields"]["fat_free_kg"],47.9)
        self.assertEqual(inbody["fields"]["bmr_kcal"],1405)
        self.assertEqual(handymet["fields"]["bmr_kcal"],1500)
        self.assertEqual(handymet["fields"]["predicted_bmr_kcal"],1400)
        self.assertEqual(handymet["fields"]["tdee_kcal"],2400)
        self.assertNotIn("waist_cm",inbody["fields"])

    def test_unrecognized_document_does_not_guess(self):
        from reportlab.pdfgen.canvas import Canvas
        from body_exams import parse_pdf
        stream=io.BytesIO();canvas=Canvas(stream);canvas.drawString(20,700,"Unknown exam document with some text and numeric values 70 kg 1200 kcal");canvas.save()
        with self.assertRaises(ValueError):parse_pdf(stream.getvalue())


if __name__=="__main__":unittest.main()
