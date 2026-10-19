import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from crm_patients import PatientDirectory
from crm_store import CrmStore


class PatientLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "patients.sqlite3"
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE clinica_patients(uuid TEXT PRIMARY KEY,name TEXT,phone TEXT,raw_json TEXT,synced_at INTEGER)")
        self.directory = PatientDirectory(self.path)
        self.store = CrmStore(Path(self.tmp.name) / "crm", self.directory.lookup)
        self.store.bootstrap({"Doctor": ["Organico"]})
        self.doctor = self.store.listing()["doctor_id"]
        self.pipeline = self.store.listing()["pipelines"][0]

    def patient(self, uid="p1", name="Nome do Clinica Experts", number="(33) 99839-8227", **raw):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO clinica_patients VALUES(?,?,?,?,?)", (uid,name,number,json.dumps(raw),1234))
        self.directory.checked = 0

    def lead(self, number="5533998398227", name="Nome digitado no WhatsApp"):
        return self.store.lead({"doctor_id":self.doctor,"pipeline_id":self.pipeline["id"],"stage_id":self.pipeline["stages"][0]["id"],
            "phone":number,"name":name,"title":"Retorno"}, "Equipe")

    def test_matches_country_and_ddd_uses_experts_name_and_does_not_write_source(self):
        self.patient();before=self.path.read_bytes()
        key=self.lead()
        lead=self.store.detail(key,self.doctor)["lead"]
        self.assertEqual(lead["name"],"Nome do Clinica Experts")
        self.assertEqual(lead["experts_uuid"],"p1")
        self.assertEqual(lead["experts_status"],"matched")
        self.assertEqual(before,self.path.read_bytes())

    def test_same_phone_and_events_reuse_contact_and_opportunity(self):
        self.patient();first=self.lead();second=self.lead(name="Outro nome de perfil")
        self.assertEqual(first,second)
        channel=self.store.channel({"doctor_id":self.doctor,"phone":"5533990000000","name":"A"})
        ch=self.store.listing()["channels"][0]
        data={"key":{"id":"m1","remoteJid":"5533998398227@s.whatsapp.net"},"pushName":"Nome do WhatsApp","message":{"conversation":"Oi"}}
        for _ in range(2): self.store.receive(ch["instance"],data,"messages.upsert")
        self.assertEqual(len(self.store.listing()["leads"]),1)
        self.assertEqual(self.store.detail(first,self.doctor)["lead"]["name"],"Nome do Clinica Experts")

    def test_shared_phone_needs_review_and_different_ddd_does_not_match(self):
        self.patient();self.patient("p2","Familiar","+55 (33) 99839-8227")
        self.assertEqual(self.directory.lookup("5533998398227")["status"],"review")
        self.assertEqual(self.directory.lookup("5511998398227")["status"],"new")
        key=self.lead();lead=self.store.detail(key,self.doctor)["lead"]
        self.assertEqual(lead["experts_uuid"],"")
        self.assertEqual(lead["experts_status"],"review")

    def test_missing_or_empty_cache_is_not_evidence_of_new_patient(self):
        self.assertEqual(self.directory.lookup("5533998398227")["status"],"unchecked")
        missing=PatientDirectory(self.path.parent/"missing.sqlite3")
        self.assertEqual(missing.lookup("5533998398227")["status"],"unchecked")
        self.assertFalse(missing.path.exists())

    def test_later_registration_links_existing_lead_without_recreating(self):
        self.patient("another","Outro","(11) 99999-8888")
        key=self.lead()
        self.assertEqual(self.store.detail(key,self.doctor)["lead"]["experts_status"],"new")
        self.patient()
        lead=self.store.listing()["leads"][0]
        self.assertEqual(lead["id"],key)
        self.assertEqual(lead["experts_uuid"],"p1")
        self.assertEqual(lead["name"],"Nome do Clinica Experts")

    def test_multiple_registered_phones_reuse_the_same_patient(self):
        self.patient(phones=[{"number":"(33) 99999-1111"}])
        first=self.lead();second=self.lead(number="5533999991111")
        self.assertEqual(first,second)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM contact_phones").fetchone()[0],2)

    def test_deleted_patients_do_not_match(self):
        self.patient(deleted_at="2026-10-18")
        self.assertNotEqual(self.directory.lookup("5533998398227")["status"],"matched")


if __name__ == "__main__":
    unittest.main()
