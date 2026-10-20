import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import uuid

from crm_patients import PatientDirectory, phone_keys
from crm_store import CrmStore


class IdentityTests(unittest.TestCase):
    modern = "5531998887777"
    legacy = "553198887777"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "patients.sqlite3"
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE clinica_patients(uuid TEXT PRIMARY KEY,name TEXT,phone TEXT,raw_json TEXT,synced_at INTEGER)")
        self.directory = PatientDirectory(self.path)
        self.store = CrmStore(Path(self.tmp.name) / "crm", self.directory.lookup, patient_directory=self.directory)
        self.store.bootstrap({"Doctor": ["Organico", "Trafego"]})
        listing = self.store.listing()
        self.doctor, self.pipelines = listing["doctor_id"], listing["pipelines"]

    def patient(self, number=None, uid="p1", name="Paciente Teste Completo"):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO clinica_patients VALUES(?,?,?,?,?)", (uid, name, number or self.modern, "{}", 1234))
        self.directory.invalidate()

    def raw_lead(self, number, pipeline=0, *, patient="", age=100, stage=0, name="Nome do WhatsApp"):
        cid, lid = uuid.uuid4().hex, uuid.uuid4().hex
        pipe = self.pipelines[pipeline]
        with self.store.db() as c:
            c.execute("INSERT INTO contacts(id,name,phone,experts_uuid,experts_status) VALUES(?,?,?,?,?)", (cid, name, number, patient, "matched" if patient else "new"))
            c.execute("INSERT INTO contact_phones VALUES(?,?)", (number, cid))
            c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (lid, self.doctor, pipe["id"], pipe["stages"][stage]["id"], cid, "Original", time.time()-age, time.time()-age))
            self.store.event(c, lid, "Equipe", "note", "Historico " + number, time.time()-age)
        return cid, lid

    def new_lead(self, number, pipeline=0):
        pipe = self.pipelines[pipeline]
        return self.store.lead({"doctor_id": self.doctor, "pipeline_id": pipe["id"], "stage_id": pipe["stages"][0]["id"], "name": "Digitado", "phone": number, "title": "Contato"}, "Equipe")

    def test_mobile_pair_preserves_country_and_ddd_but_not_landlines(self):
        self.assertEqual(set(phone_keys(self.modern)), {self.modern, self.legacy})
        self.assertEqual(set(phone_keys(self.legacy)), {self.modern, self.legacy})
        self.assertEqual(phone_keys("553132305550"), ("553132305550",))
        self.assertEqual(phone_keys("12025550123"), ("12025550123",))
        self.assertNotIn(self.modern, phone_keys("5511998887777"))

    def test_directory_links_old_number_and_searches_both_formats(self):
        self.patient()
        self.assertEqual(self.directory.lookup(self.legacy)["uuid"], "p1")
        self.assertEqual(self.directory.search(self.legacy)["total"], 1)
        cid, lid = self.raw_lead(self.legacy)
        rows = self.store.listing(filters={"search": self.modern})["leads"]
        self.assertEqual([r["id"] for r in rows], [lid])
        self.assertEqual((rows[0]["name"], rows[0]["experts_uuid"]), ("Paciente Teste Completo", "p1"))
        self.assertEqual(self.new_lead(self.modern), lid)

    def test_different_patients_on_two_formats_are_ambiguous_even_on_exact_match(self):
        self.patient()
        self.patient(self.legacy, "p2", "Outro Paciente")
        self.assertEqual(self.directory.lookup(self.modern)["status"], "review")
        self.assertEqual(self.directory.lookup(self.legacy)["status"], "review")
        result = self.directory.search()
        self.assertTrue(all(p["shared_phone"] and not p["phones"] for p in result["items"]))
        self.raw_lead(self.legacy)
        self.raw_lead(self.modern)
        self.assertEqual(len(self.store.listing()["leads"]), 2)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contact_merges").fetchone()[0], 0)

    def test_existing_duplicate_is_consolidated_preserving_stage_notes_optout_and_messages(self):
        self.patient()
        first, old = self.raw_lead(self.legacy, stage=2, age=200)
        second, manual = self.raw_lead(self.modern, patient="p1", age=10)
        channel = self.store.channel({"doctor_id": self.doctor, "phone": "5531990001111", "name": "Teste"})
        with self.store.db() as c:
            c.execute("UPDATE contacts SET opted_out=1,service_location='capelinha',service_location_source='manual' WHERE id=?", (second,))
            for i, cid in enumerate((first, second)):
                c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at,provider_id) VALUES(?,?,?,?,'in','received',?,?)", ("m"+str(i), channel, cid, "Mensagem preservada", time.time(), "provider"+str(i)))
        listing = self.store.listing()
        backup_path = self.store.path.parent / "backups" / "before-phone-identity-v1.sqlite3"
        self.assertTrue(backup_path.exists())
        with sqlite3.connect(backup_path) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM leads WHERE deleted_at=0").fetchone()[0], 2)
        self.assertEqual(len(listing["leads"]), 1)
        lead = listing["leads"][0]
        self.assertEqual((lead["contact_id"], lead["stage_id"], lead["opted_out"]), (first, self.pipelines[0]["stages"][2]["id"], 1))
        self.assertEqual((lead["name"], lead["experts_uuid"], lead["service_location_source"]), ("Paciente Teste Completo", "p1", "manual"))
        detail = self.store.detail(lead["id"], self.doctor)
        self.assertEqual(len(detail["messages"]), 2)
        self.assertEqual(sum(e["kind"] == "note" for e in detail["events"]), 2)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM leads").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM contact_merges").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM bot_events WHERE processed=0").fetchone()[0], 0)
            backup = json.loads(c.execute("SELECT snapshot FROM contact_merges").fetchone()[0])
            self.assertEqual(len(backup["contacts"]), 2)
            self.assertFalse(c.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(self.new_lead(self.modern), old)
        self.assertEqual(self.new_lead(self.legacy), old)
        self.store.listing()
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contact_merges").fetchone()[0], 1)

    def test_opportunities_in_different_funnels_share_one_contact_without_disappearing(self):
        self.patient()
        first, _ = self.raw_lead(self.legacy, age=100)
        second, _ = self.raw_lead(self.modern, pipeline=1, patient="p1", age=10)
        rows = self.store.listing()["leads"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["contact_id"] for r in rows}, {first})
        self.assertEqual({r["experts_uuid"] for r in rows}, {"p1"})
        self.assertEqual(self.new_lead(self.modern, pipeline=1), next(r["id"] for r in rows if r["pipeline_id"] == self.pipelines[1]["id"]))

    def test_conflicting_established_patient_is_not_reassigned(self):
        self.patient()
        self.raw_lead(self.legacy, patient="previous")
        self.raw_lead(self.modern, patient="p1")
        self.assertEqual(len(self.store.listing()["leads"]), 2)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contact_merges").fetchone()[0], 0)

    def test_unconfirmed_secondary_phone_blocks_automatic_merge(self):
        self.patient()
        first, _ = self.raw_lead(self.legacy)
        self.raw_lead(self.modern, patient="p1")
        with self.store.db() as c:
            c.execute("INSERT INTO contact_phones VALUES('5511999991111',?)", (first,))
        self.assertEqual(len(self.store.listing()["leads"]), 2)

    def test_course_opportunity_keeps_its_identity_and_is_not_given_clinical_finances(self):
        self.patient()
        first, _ = self.raw_lead(self.legacy)
        self.store.bootstrap({"Doctor": ["REGENCODE"]})
        with self.store.db() as c:
            c.execute("UPDATE pipelines SET institute_key='victor-paranhos',course_key='regen-code' WHERE name='REGENCODE'")
        course = next(p for p in self.store.listing()["pipelines"] if p["course_key"])
        key = self.store.lead({"doctor_id": self.doctor, "pipeline_id": course["id"], "stage_id": course["stages"][0]["id"],
            "phone": self.modern, "name": "Nome do Aluno", "title": "Curso", "email": "aluno@example.test"}, "Equipe")
        lead = self.store.detail(key, self.doctor)["lead"]
        self.assertEqual((lead["contact_id"], lead["name"], lead["email"]), (first, "Nome do Aluno", "aluno@example.test"))
        self.assertEqual(lead["customer_value"]["state"], "course")
        self.assertEqual(len(self.store.listing()["leads"]), 2)

    def test_missing_directory_does_not_merge_contacts_by_name_or_guess_identity(self):
        self.raw_lead(self.legacy, name="Mesmo Nome")
        self.raw_lead(self.modern, name="Mesmo Nome")
        self.assertEqual(len(self.store.listing()["leads"]), 2)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contact_merges").fetchone()[0], 0)

    def test_sent_campaign_history_is_preserved_and_queued_duplicates_are_not_sent(self):
        self.patient()
        first, _ = self.raw_lead(self.legacy, age=200)
        second, _ = self.raw_lead(self.modern, patient="p1", age=10)
        channel = self.store.channel({"doctor_id": self.doctor, "phone": "5531990001111", "name": "Teste"})
        with self.store.db() as c:
            c.execute("INSERT INTO campaigns(id,doctor_id,channel_id,name,body,interval_seconds,start_at,hour_from,hour_to,created_at,created_by) VALUES('campaign',?,?,'Teste','Texto',60,0,8,18,0,'Equipe')", (self.doctor, channel))
            for i, cid in enumerate((first, second)):
                c.execute("INSERT INTO messages(id,channel_id,contact_id,campaign_id,body,direction,status,created_at) VALUES(?,?,?,'campaign','Enviada','out','sent',0)", ("sent"+str(i), channel, cid))
                c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at) VALUES(?,?,?,'Pendente','out','queued',0)", ("pending"+str(i), channel, cid))
        self.store.reconcile_patients()
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE contact_id=?", (first,)).fetchone()[0], 4)
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE status='sent'").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE status='queued'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE status='skipped'").fetchone()[0], 2)
            self.assertFalse(c.execute("PRAGMA foreign_key_check").fetchall())

    def test_in_flight_message_defers_merge_until_the_send_is_confirmed(self):
        self.patient()
        first, _ = self.raw_lead(self.legacy)
        self.raw_lead(self.modern, patient="p1")
        channel = self.store.channel({"doctor_id": self.doctor, "phone": "5531990001111", "name": "Teste"})
        with self.store.db() as c:
            c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at) VALUES('inflight',?,?,'Texto','out','sending',0)", (channel, first))
        self.assertEqual(len(self.store.listing()["leads"]), 2)
        with self.store.db() as c:
            c.execute("UPDATE messages SET status='sent' WHERE id='inflight'")
        self.assertEqual(len(self.store.listing()["leads"]), 1)

    def test_original_stage_survives_even_if_canonical_contact_was_oldest_in_another_funnel(self):
        self.patient()
        first, _ = self.raw_lead(self.modern, pipeline=1, patient="p1", age=1000)
        with self.store.db() as c:
            pipe = self.pipelines[0]
            c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,created_at,updated_at) VALUES('new-manual',?,?,?,?,'Manual',?,?)", (self.doctor, pipe["id"], pipe["stages"][0]["id"], first, time.time()-10, time.time()-10))
        self.raw_lead(self.legacy, stage=2, age=200)
        rows = self.store.listing()["leads"]
        self.assertEqual(len(rows), 2)
        organic = next(r for r in rows if r["pipeline_id"] == self.pipelines[0]["id"])
        self.assertEqual(organic["stage_id"], self.pipelines[0]["stages"][2]["id"])
        self.assertEqual(organic["contact_id"], first)

    def test_existing_course_only_duplicates_do_not_require_a_clinical_record(self):
        first, _ = self.raw_lead(self.legacy, age=200)
        self.raw_lead(self.modern, age=10)
        with self.store.db() as c:
            c.execute("UPDATE pipelines SET institute_key='victor-paranhos',course_key='regen-code'")
        self.store.patient_lookup = lambda n: self.fail("Courses cannot query clinical identity")
        rows = self.store.listing()["leads"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contact_id"], first)
        self.assertEqual(rows[0]["customer_value"]["state"], "course")
        self.assertEqual(rows[0]["experts_uuid"], "")


if __name__ == "__main__":
    unittest.main()
