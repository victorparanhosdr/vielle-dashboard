import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crm_commercial import PatientCommercial
from crm_patients import PatientDirectory
from crm_store import CrmStore


class CommercialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"experts.sqlite3"
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE clinica_patients(uuid TEXT PRIMARY KEY,name TEXT,phone TEXT,raw_json TEXT,synced_at INTEGER)")
            c.execute("INSERT INTO clinica_patients VALUES('patient','Paciente','5500000000001','{}',1)")
            c.execute("CREATE TABLE clinica_sales(uuid TEXT PRIMARY KEY,patient_uuid TEXT,type TEXT,sale_date TEXT,total REAL,raw_json TEXT,synced_at INTEGER)")
        self.reader=PatientCommercial(self.path,{"Doutor A":"doc-a"})
        self.directory=PatientDirectory(self.path)
        self.store=CrmStore(Path(self.tmp.name)/"crm",self.directory.lookup,self.reader.lookup,commercial_source=self.reader)
        self.store.bootstrap({"Doutor A":["Funil"],"Doutor B":["Funil"]})
        self.doctor=next(d["id"] for d in self.store.listing()["doctors"] if d["name"]=="Doutor A")
        p=self.store.listing(self.doctor)["pipelines"][0]
        self.lead=self.store.lead({"doctor_id":self.doctor,"pipeline_id":p["id"],"stage_id":p["stages"][0]["id"],
            "name":"Paciente","phone":"5500000000001","title":"Avaliação"},"Equipe")

    def add(self,uid,kind="sale",status="active",patient="patient",**changes):
        raw={"type":kind,"status":status,"buyer":{"uuid":patient},"seller":{"uuid":"doc-a","name":"Doutor A"},"final_amount":123456,**changes}
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO clinica_sales VALUES(?,?,?,?,?,?,?)",(uid,patient,kind,"2024-03-01",1234.56,json.dumps(raw),123))

    def data(self,**changes):
        return self.store.commercial(self.lead,self.doctor,**changes)

    def value(self):
        return self.store.listing(self.doctor)["leads"][0]["customer_value"]

    def test_customer_tiers_exact_cent_boundaries_and_not_opportunity_value(self):
        self.add("sale",final_amount=499999)
        self.assertEqual(self.value()["tier"],"bronze")
        self.assertEqual(self.value()["remaining_cents"],1)
        for amount,tier in ((500000,"silver"),(1499999,"silver"),(1500000,"gold")):
            with sqlite3.connect(self.path) as c:
                raw=json.loads(c.execute("SELECT raw_json FROM clinica_sales WHERE uuid='sale'").fetchone()[0])
                raw["final_amount"]=amount
                c.execute("UPDATE clinica_sales SET raw_json=? WHERE uuid='sale'",(json.dumps(raw),))
            self.assertEqual(self.value()["tier"],tier)
            self.assertEqual(self.value()["sales_cents"],amount)
        with self.store.db() as c:c.execute("UPDATE leads SET value_cents=99999999")
        self.assertEqual(self.store.detail(self.lead,self.doctor)["lead"]["customer_value"]["sales_cents"],1500000)

    def test_customer_tiers_doctor_scope_valid_sales_and_complete_history(self):
        for i in range(26):self.add(f"sale-{i}",final_amount=20000)
        self.add("other",seller={"uuid":"other-doctor","name":"Outro"},final_amount=99999999)
        self.add("quote",kind="sale_quote",status="open",final_amount=99999999)
        self.add("won",kind="sale_quote",status="won",final_amount=99999999)
        self.add("cancelled",status="cancelled",final_amount=99999999)
        self.add("deleted",deleted=True,final_amount=99999999)
        self.add("refund",status="refunded",final_amount=99999999)
        value=self.value()
        self.assertEqual(value["tier"],"silver")
        self.assertEqual(value["sales_cents"],520000)
        self.assertEqual(value["sales_count"],26)
        self.assertEqual(value["scope"],"doctor")

    def test_partial_history_has_no_tier_and_bad_quotes_do_not_block_sales_tier(self):
        self.add("sale",final_amount=800000)
        self.add("bad-quote",kind="sale_quote",status="open",seller={})
        self.assertEqual(self.value()["tier"],"silver")
        self.add("bad-sale",seller={})
        self.assertEqual(self.value()["state"],"partial")
        self.assertIsNone(self.value()["tier"])
        self.assertEqual(self.value()["sales_cents"],800000)

    def test_tier_rules_validate_persist_and_refresh_without_rewriting_sales(self):
        self.add("sale",final_amount=600000)
        before=self.path.read_bytes()
        self.store.save_customer_tier_rules({"silver_cents":200000,"gold_cents":600000})
        self.assertEqual(self.value()["tier"],"gold")
        for silver,gold in ((0,100),(100,100),(200,100),(True,200),(1,1.5)):
            with self.assertRaises(ValueError):self.store.save_customer_tier_rules({"silver_cents":silver,"gold_cents":gold})
        reopened=CrmStore(Path(self.tmp.name)/"crm")
        self.assertEqual(reopened.listing(self.doctor)["customer_tier_rules"],{"silver_cents":200000,"gold_cents":600000})
        self.assertEqual(self.path.read_bytes(),before)

    def test_unavailable_and_unsafe_customer_values_never_default_to_bronze(self):
        self.assertEqual(self.value()["tier"],"bronze")  # Known patient, valid empty history.
        self.store.commercial_source=PatientCommercial(Path(self.tmp.name)/"missing.sqlite3")
        self.assertEqual(self.value()["state"],"unavailable")
        self.assertIsNone(self.value()["tier"])
        self.store.commercial_source=self.reader
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO clinica_patients VALUES('duplicate','Outro','5500000000001','{}',1)")
        self.directory.checked=0
        self.assertEqual(self.value()["state"],"identity_pending")
        self.assertIsNone(self.value()["tier"])

    def test_customer_summaries_are_batched_single_read_only_snapshot(self):
        with sqlite3.connect(self.path) as c:
            c.executemany("INSERT INTO clinica_patients VALUES(?,?,?,?,?)",[(f"p-{i}","Paciente","","{}",1) for i in range(405)])
        self.add("sale",final_amount=700000)
        before=self.path.read_bytes();connect=sqlite3.connect;connections=[];queries=[]
        def tracked(*args,**kwargs):
            db=connect(*args,**kwargs);db.set_trace_callback(queries.append);connections.append(db);return db
        with patch("crm_commercial.sqlite3.connect",side_effect=tracked):
            data=self.reader.summaries(["patient",*[f"p-{i}" for i in range(405)],"missing-patient"],"Doutor A")
        self.assertEqual(len(connections),1)
        self.assertEqual(sum("FROM clinica_sales" in query for query in queries),2)
        self.assertEqual(data["patient"]["summary"]["sales_cents"],700000)
        self.assertEqual(data["missing-patient"]["state"],"identity_pending")
        self.assertEqual(data["p-404"]["summary"]["sales_cents"],0)
        self.assertEqual(self.path.read_bytes(),before)

    def test_exact_uuid_all_years_real_totals_read_only(self):
        self.add("sale",procedures=[{"name":"Toxina"}]);self.add("other-patient",patient="someone-else")
        before=self.path.read_bytes();data=self.data()
        self.assertEqual(data["state"],"ready");self.assertEqual(data["summary"]["sales_cents"],123456)
        self.assertEqual(data["items"][0]["date"],"2024-03-01")
        self.assertEqual(data["items"][0]["procedures"],["Toxina"])
        self.assertEqual(self.path.read_bytes(),before)

    def test_pending_statuses_exclude_won_converted_cancelled_and_deleted(self):
        for status in ("open","lost","expired","won"):
            self.add(status,kind="sale_quote",status=status)
        self.add("converted",kind="sale_quote",status="open")
        self.add("closed-sale",sale_quote_uuid="converted")
        self.add("deleted",kind="sale_quote",deleted_at="2026-10-18")
        self.add("cancelled",status="cancelled")
        data=self.data(kind="quotes")
        self.assertEqual({item["status"] for item in data["items"]},{"active","lost","expired"})
        self.assertEqual(data["summary"]["quotes_count"],3)
        self.assertEqual(data["summary"]["sales_count"],1)

    def test_other_purchase_is_not_evidence_quote_won(self):
        self.add("open",kind="sale_quote",status="open")
        self.add("sale",sale_quote_uuid="different-quote")
        self.assertEqual(self.data(kind="quotes")["total"],1)

    def test_professional_scope_no_guessing_and_whole_clinic_explicit(self):
        self.add("own");self.add("other",seller={"uuid":"doc-b","name":"Doutor A"})
        self.add("unknown",seller={})
        self.assertEqual(self.data()["summary"]["sales_count"],1)
        self.assertEqual(self.data()["warnings"]["professional"],1)
        self.assertEqual(self.data(scope="clinic")["summary"]["sales_count"],3)
        other=next(d["id"] for d in self.store.listing()["doctors"] if d["name"]=="Doutor B")
        with self.assertRaises(ValueError):self.store.commercial(self.lead,other)

    def test_mismatched_patient_identity_never_uses_name_or_phone(self):
        self.add("bad",buyer={"uuid":"different","name":"Paciente","phone":"5500000000001"})
        self.assertEqual(self.data()["summary"]["sales_count"],0)
        self.assertEqual(self.data()["warnings"]["identity"],1)

    def test_inactive_refunded_missing_amount_and_zero(self):
        self.add("inactive",status="inactive");self.add("deleted",deleted=True);self.add("refund",status="refunded")
        self.add("zero",final_amount=0)
        self.add("missing",final_amount=None)
        with sqlite3.connect(self.path) as c:c.execute("UPDATE clinica_sales SET total=NULL WHERE uuid='missing'")
        data=self.data();self.assertEqual(data["summary"]["sales_count"],2)
        self.assertEqual(data["summary"]["sales_cents"],0);self.assertEqual(data["warnings"]["amount"],1)
        self.assertEqual(next(i for i in data["items"] if i["uuid"]=="zero")["amount_cents"],0)

    def test_pagination_totals_and_invalid_filters(self):
        for i in range(26):self.add(f"sale-{i}")
        self.assertEqual(len(self.data()["items"]),25)
        second=self.data(page=2)
        self.assertEqual(len(second["items"]),1);self.assertEqual(second["summary"]["sales_count"],26)
        for changes in ({"scope":"other"},{"kind":"bad"},{"page":0}):
            with self.assertRaises(ValueError):self.data(**changes)

    def test_unsafe_patient_link_and_unavailable_base_are_not_zero_history(self):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO clinica_patients VALUES('duplicate','Outro','5500000000001','{}',1)")
        self.directory.checked=0
        self.assertEqual(self.data()["state"],"identity_pending")
        absent=Path(self.tmp.name)/"missing.sqlite3"
        self.assertEqual(PatientCommercial(absent).lookup("patient","Doutor A","doctor","sales",1)["state"],"unavailable")
        self.assertFalse(absent.exists())
