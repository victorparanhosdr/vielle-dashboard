import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from crm_access import crm_access
from crm_store import CrmStore, Conflict
import crm_clinic_journey as journey
import crm_patient_opportunities as deals
from crm_dashboard import dashboard
from crm_bulk import preview as bulk_preview, apply as bulk_apply


class ClinicJourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(Path(self.tmp.name))
        self.store.bootstrap({"Doutor Exemplo": ["Clínica", "RegenCode", "Outro funil"]})
        data = self.store.listing()
        self.doctor = data["doctor_id"]
        self.pipeline = next(p for p in data["pipelines"] if p["name"] == "Clínica")
        self.course = next(p for p in data["pipelines"] if p["name"] == "RegenCode")
        self.actor = {"id": "manager-test", "nome": "Gestor Exemplo", "is_master": True}
        self.number = 0

    def lead(self, stage=0, pipeline=None):
        self.number += 1
        p = pipeline or self.pipeline
        return self.store.lead({"doctor_id": self.doctor, "pipeline_id": p["id"], "stage_id": p["stages"][stage]["id"],
            "name": "Pessoa Exemplo", "phone": "5500000"+str(self.number).zfill(6), "title": "Interesse de exemplo", "value_cents": 12300}, "Equipe")

    def migrate(self):
        preview = journey.preview(self.store, self.doctor, self.pipeline["id"], self.actor)
        return journey.apply(self.store, self.doctor, {"id": preview["id"], "confirm": True}, self.actor)

    def update(self, ident, stage, **payload):
        lead = self.store.detail(ident, self.doctor)["lead"]
        return self.store.lead({**lead, "stage_id": stage, **payload}, "Equipe")

    def test_migration_preserves_ids_events_dates_and_course_and_creates_private_backup(self):
        ident, course = self.lead(4), self.lead(1, self.course)
        with self.store.db() as c:
            original = dict(c.execute("SELECT * FROM leads WHERE id=?", (ident,)).fetchone())
            course_before = dict(c.execute("SELECT * FROM leads WHERE id=?", (course,)).fetchone())
            history = [tuple(r) for r in c.execute("SELECT * FROM events WHERE lead_id=?", (ident,))]
        result = self.migrate()
        current = self.store.detail(ident, self.doctor)["lead"]
        self.assertEqual(current["stage_id"], result["stage_ids"]["patients"])
        self.assertEqual(current["created_at"], original["created_at"])
        self.assertEqual(current["updated_at"], original["updated_at"])
        backup = self.store.path.parent / "backups" / result["backup"]
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as c:
            self.assertEqual(c.execute("SELECT stage_id FROM leads WHERE id=?", (ident,)).fetchone()[0], original["stage_id"])
        with self.store.db() as c:
            self.assertEqual(dict(c.execute("SELECT * FROM leads WHERE id=?", (course,)).fetchone()), course_before)
            self.assertTrue(all(c.execute("SELECT 1 FROM events WHERE id=?", (r[0],)).fetchone() for r in history))
            self.assertEqual(c.execute("SELECT count(*) FROM leads").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(len(journey.active_stages(c,self.pipeline["id"])), 8)

    def test_courses_are_rejected_even_without_course_binding(self):
        for operation in (journey.preview,):
            with self.assertRaises(ValueError):
                operation(self.store, self.doctor, self.course["id"], self.actor)
        ident = self.lead(pipeline=self.course)
        with self.assertRaises(ValueError):
            deals.listing(self.store, self.doctor, ident)

    def test_preview_is_frozen_scoped_and_idempotent(self):
        ident = self.lead()
        p = journey.preview(self.store, self.doctor, self.pipeline["id"], self.actor)
        with self.assertRaises(LookupError):
            journey.apply(self.store,self.doctor,{"id":p["id"],"confirm":True},{**self.actor,"id":"other"})
        self.update(ident,self.pipeline["stages"][1]["id"])
        with self.assertRaises(Conflict):
            journey.apply(self.store,self.doctor,{"id":p["id"],"confirm":True},self.actor)
        p = journey.preview(self.store,self.doctor,self.pipeline["id"],self.actor)
        one = journey.apply(self.store,self.doctor,{"id":p["id"],"confirm":True},self.actor)
        self.assertEqual(one,journey.apply(self.store,self.doctor,{"id":p["id"],"confirm":True},self.actor))

    def test_mapping_never_calls_objection_or_silence_refusal(self):
        for name in ("BOT: 24 horas", "Lead Morto", "Achou caro", "Sem verba no momento", "Retornar depois"):
            self.assertEqual(journey.mapping({"name": name, "kind":"open"}), ("service","",True))
        self.assertEqual(journey.mapping({"name":"Fornecedor","kind":"open"}), ("noncommercial","supplier",False))

    def test_legacy_lost_and_trash_keep_history_without_fabricated_reason(self):
        ident = self.lead(5)
        with self.store.db() as c:
            c.execute("UPDATE leads SET deleted_at=123,deleted_by='Equipe' WHERE id=?",(ident,))
        result = self.migrate()
        with self.store.db() as c:
            row = dict(c.execute("SELECT * FROM leads WHERE id=?",(ident,)).fetchone())
            self.assertEqual(row["deleted_at"],123)
            self.assertEqual(row["stage_id"],result["stage_ids"]["closed"])
            self.assertEqual(journey.state(c,ident)["reason_id"],"legacy_unclassified")
            self.assertTrue(journey.state(c,ident)["needs_review"])

    def test_closure_requires_correct_reason_and_reopening_preserves_audit(self):
        ident = self.lead();ids=self.migrate()["stage_ids"]
        for payload in (None, {"reason_id":"supplier"}, {"reason_id":"no_response","note":"Tentativas revisadas"}):
            with self.assertRaises(ValueError):
                self.update(ident,ids["closed"],closure=payload)
        self.update(ident,ids["closed"],closure={"reason_id":"no_response","note":"Tentativas revisadas", "confirm_attempts":True})
        self.update(ident,ids["service"])
        with self.store.db() as c:
            self.assertEqual(journey.state(c,ident)["reason_id"],"")
            self.assertGreaterEqual(c.execute("SELECT count(*) FROM clinic_journey_events WHERE lead_id=?",(ident,)).fetchone()[0],2)

    def test_noncommercial_is_outside_conversion_and_forecast_but_retains_card(self):
        seller, patient = self.lead(), self.lead(4)
        ids = self.migrate()["stage_ids"]
        self.update(seller,ids["noncommercial"],closure={"reason_id":"supplier","note":"Oferta de produto"})
        result = dashboard(self.store,self.doctor)
        funnel = next(p for p in result["funnels"] if p["id"]==self.pipeline["id"])
        self.assertEqual((funnel["total"],funnel["won"],funnel["lost"],funnel["noncommercial"]),(1,1,0,1))
        board=self.store.listing(self.doctor,kanban_pipeline=self.pipeline["id"])["kanban"]["summary"]
        self.assertEqual((board["total"],board["commercial_total"],board["noncommercial"]),(2,1,1))
        self.assertEqual(board["value_cents"],12300)
        with self.assertRaises(ValueError):
            deals.save(self.store,self.doctor,self.deal_payload(seller),self.actor)

    def deal_payload(self, lead):
        return {"lead_id":lead,"title":"Nova negociação","value_cents":10000,"status":"open","note":"",
                "request_id":uuid.uuid4().hex,"confirm_new_negotiation":True,
                "patient_revision":self.store.detail(lead,self.doctor)["lead"]["revision"]}

    def test_new_negotiation_keeps_patient_card_and_idempotent_receipt(self):
        ident=self.lead(4);ids=self.migrate()["stage_ids"]
        before=self.store.detail(ident,self.doctor)["lead"]
        payload=self.deal_payload(ident)
        deal=deals.save(self.store,self.doctor,payload,self.actor)
        self.assertEqual(deal,deals.save(self.store,self.doctor,payload,self.actor))
        after=self.store.detail(ident,self.doctor)["lead"]
        self.assertEqual((after["stage_id"],after["revision"]),(ids["patients"],before["revision"]))
        self.assertEqual(len(self.store.listing(self.doctor)["leads"]),1)
        self.assertEqual(len(deals.listing(self.store,self.doctor,ident)["items"]),2)

    def test_deal_closure_and_revision_do_not_close_patient(self):
        ident=self.lead(4);ids=self.migrate()["stage_ids"]
        payload=self.deal_payload(ident);deal=deals.save(self.store,self.doctor,payload,self.actor)
        changed={**payload,"id":deal,"revision":1,"status":"lost","closure":{"reason_id":"value","note":"Recusa expressa"}}
        deals.save(self.store,self.doctor,changed,self.actor)
        with self.assertRaises(Conflict):deals.save(self.store,self.doctor,changed,self.actor)
        self.assertEqual(self.store.detail(ident,self.doctor)["lead"]["stage_id"],ids["patients"])
        self.assertEqual(deals.listing(self.store,self.doctor,ident)["summary"]["open"],0)

    def test_restricted_scope_cannot_read_patient_or_its_other_funnel_deals(self):
        ident=self.lead();self.migrate()
        with crm_access({self.doctor:[]}, self.doctor):
            with self.assertRaises(PermissionError):deals.listing(self.store,self.doctor,ident)

    def test_bulk_freezes_reason_and_reopens_without_erasing_events(self):
        ident=self.lead();ids=self.migrate()["stage_ids"]
        payload={"doctor_id":self.doctor,"pipeline_id":self.pipeline["id"],"action":"move",
            "selection":{"lead_ids":[ident]},"stage_id":ids["noncommercial"],"closure":{"reason_id":"team","note":"Contato interno"}}
        batch=bulk_preview(self.store,payload,self.actor)
        bulk_apply(self.store,{"id":batch["id"],"doctor_id":self.doctor,"confirm":True},self.actor)
        with self.store.db() as c:self.assertEqual(journey.state(c,ident)["reason_id"],"team")

    def test_legacy_form_entry_starts_in_active_stage_not_hidden_column(self):
        ids=self.migrate()["stage_ids"]
        ident=self.lead()
        self.assertEqual(self.store.detail(ident,self.doctor)["lead"]["stage_id"],ids["new"])

    def test_sale_and_quote_evidence_are_doctor_scoped_and_do_not_create_revenue(self):
        ident=self.lead()
        with self.store.db() as c:
            c.execute("UPDATE contacts SET experts_uuid='synthetic-patient',experts_status='matched' WHERE id=(SELECT contact_id FROM leads WHERE id=?)",(ident,))
        class Commercial:
            def summaries(inner,patients,doctor,include_search=False):
                self.assertEqual(doctor,"Doutor Exemplo")
                return {uid:{"state":"ready","summary":{"sales_count":1,"sales_cents":50000,"pending_quotes_count":1,"pending_quotes_cents":90000},"sales_warnings":{},"quote_warnings":{}} for uid in patients}
        self.store.commercial_source=Commercial()
        ids=self.migrate()["stage_ids"]
        self.assertEqual(self.store.detail(ident,self.doctor)["lead"]["stage_id"],ids["patients"])
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM xp").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM bot_events WHERE processed=0").fetchone()[0],0)

    def test_archived_stage_cannot_be_target_of_manual_or_bot_move(self):
        ident=self.lead();self.migrate()
        with self.assertRaises(ValueError):self.update(ident,self.pipeline["stages"][1]["id"])
        with self.store.db() as c:
            lead=self.store.owned(c,"leads",ident,self.doctor)
            target=self.store.row(c,"stages",self.pipeline["stages"][2]["id"])
            with self.assertRaises(ValueError):journey.prepare(c,lead,target)

    def test_review_filter_and_explicit_acknowledgment_preserve_original_event(self):
        ident=self.lead()
        with self.store.db() as c:
            c.execute("UPDATE stages SET name='BOT: 24 horas' WHERE id=?",(self.pipeline["stages"][0]["id"],))
        ids=self.migrate()["stage_ids"]
        self.assertEqual(len(self.store.listing(self.doctor,{"journey_review":True})["leads"]),1)
        self.update(ident,ids["service"])
        self.assertEqual(len(self.store.listing(self.doctor,{"journey_review":True})["leads"]),1)
        self.update(ident,ids["service"],closure={"review_confirmed":True})
        self.assertEqual(len(self.store.listing(self.doctor,{"journey_review":True})["leads"]),0)
        self.assertTrue(any(e["kind"]=="journey_migration" for e in self.store.detail(ident,self.doctor)["events"]))


if __name__ == "__main__":
    unittest.main()
