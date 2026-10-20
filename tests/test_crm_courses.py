import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from crm_access import institute_access
from crm_courses import CourseCommercial, refresh
from crm_store import CrmStore
from crm_kommo_import import prepare, commit
from institute_store import InstituteStore


class CourseCrmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.institutes = InstituteStore(Path(self.tmp.name) / "institute.sqlite3")
        self.institutes.initialize()
        with self.institutes.connection() as c:
            c.execute("UPDATE courses SET product_ids=? WHERE key='regen-code'", (json.dumps(["online"]),))
        self.source = CourseCommercial(self.institutes)
        self.lookup = Mock(return_value={"status": "new"})
        self.store = CrmStore(Path(self.tmp.name) / "crm", self.lookup, course_source=self.source)
        self.store.bootstrap({"Victor Paranhos de Andrade": ["Organico Paranhos", "REGENCODE"]})
        self.doc = self.store.listing()["doctors"][0]["id"]
        catalog = self.store.listing(self.doc)["pipelines"]
        self.course = next(p for p in catalog if p["course_key"])
        self.clinic = next(p for p in catalog if not p["course_key"])
        self.records([self.order()])

    def order(self, **values):
        return {"id": "order-1", "product_id": "online", "status": "paid", "name": "Aluno Exemplo",
                "email": "aluno@example.test", "phone": "5511999990000", "gross": 20000, "net": 18000,
                "currency": "BRL", "net_currency": "BRL", "created_day": "2026-10-01", "approved_day": "2026-10-02", **values}

    def records(self, rows):
        self.institutes.save_records("victor-paranhos", "regen-code", "kiwify", rows)
        self.institutes.set_sync_state("victor-paranhos", "regen-code", "kiwify", {"ok": True, "at": 100})
        self.source.cache.clear()

    def lead(self, pipeline=None, **values):
        p = pipeline or self.course
        return self.store.lead({"doctor_id": self.doc, "pipeline_id": p["id"], "stage_id": p["stages"][0]["id"],
                               "name": "Aluno Exemplo", "phone": "5511999990000", "email": "aluno@example.test",
                               "title": "Curso CO2", **values}, "Master")

    def summary(self, emails=None, phones=None):
        return self.source.summary("victor-paranhos", "regen-code", emails or [], phones or [])

    def test_course_never_looks_up_clinical_data(self):
        self.lookup.side_effect = AssertionError("Course must not query Experts")
        key = self.lead()
        data = self.store.detail(key, self.doc)["lead"]
        self.store.listing(self.doc)
        self.assertEqual(data["course_data"]["paid_cents"], 20000)
        self.assertEqual(data["customer_value"]["state"], "course")
        self.assertEqual(self.store.commercial(key, self.doc)["state"], "course")
        self.lookup.assert_not_called()

    def test_exact_email_or_full_phone_only_and_product_scope(self):
        self.records([self.order(), self.order(id="presencial", product_id="another", gross=999999),
                      self.order(id="other-ddd", phone="5533999990000", email="other@example.test")])
        self.assertEqual(self.summary(["ALUNO@example.test"])["paid_cents"], 20000)
        self.assertEqual(self.summary(phones=["5511999990000"])["paid_count"], 1)
        self.assertEqual(self.summary(phones=["5531999990000"])["paid_count"], 0)

    def test_shared_phone_is_not_automatically_linked_to_multiple_students(self):
        self.records([self.order(), self.order(id="other", email="family@example.test")])
        self.assertEqual(self.summary(phones=["5511999990000"])["state"], "ambiguous")
        self.assertEqual(self.summary(["aluno@example.test"], ["5511999990000"])["paid_count"], 1)
        self.assertEqual(self.summary(["wrong@example.test"], ["5511999990000"])["paid_count"], 0)

    def test_refunds_pending_and_unknown_values_are_not_paid(self):
        self.records([self.order(), self.order(id="refund", status="refunded"),
                      self.order(id="pending", status="waiting_payment"), self.order(id="invalid", gross=None, net=None)])
        result = self.summary(["aluno@example.test"])
        self.assertEqual((result["paid_count"], result["paid_cents"], result["refunded_count"], result["pending_count"]), (2, 20000, 1, 1))
        self.assertTrue(result["partial"])
        self.assertFalse(result["net_complete"])

    def test_contact_reused_across_clinic_and_course_and_optout_preserved(self):
        clinical = self.lead(self.clinic)
        with self.store.db() as c:
            c.execute("UPDATE contacts SET opted_out=1")
        course = self.lead(name="Nome no curso")
        one, two = [self.store.detail(key, self.doc)["lead"] for key in (clinical, course)]
        self.assertEqual(one["contact_id"], two["contact_id"])
        self.assertEqual(two["opted_out"], 1)
        self.assertEqual(two["name"], "Nome no curso")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0], 1)

    def test_institute_access_is_required_for_structured_course_data(self):
        key = self.lead()
        with institute_access([]):
            self.assertNotIn(self.course["id"], [p["id"] for p in self.store.listing(self.doc)["pipelines"]])
            with self.assertRaises(PermissionError):
                self.store.detail(key, self.doc)
            with self.assertRaises(PermissionError):
                self.lead(phone="5511999990001")
        with institute_access(["victor-paranhos"]):
            self.assertEqual(self.store.detail(key, self.doc)["lead"]["course_data"]["paid_count"], 1)

    def test_single_conversation_can_have_two_separate_opportunities(self):
        clinical = self.lead(self.clinic)
        course = self.lead()
        channel = self.store.channel({"doctor_id": self.doc, "name": "Atendimento", "phone": "5511999998888"})
        with self.store.db() as c:
            instance = self.store.row(c, "channels", channel)["instance"]
        self.store.receive(instance, {"key": {"id": "msg-1", "remoteJid": "5511999990000@s.whatsapp.net"}, "message": {"conversation": "Ola"}}, "messages.upsert")
        self.assertEqual(len(self.store.inbox(self.doc, 1)["conversations"]), 1)
        for kind, key in (("clinic", clinical), ("course", course)):
            row = self.store.inbox(self.doc, 1, business=kind)["conversations"][0]
            self.assertEqual(row["lead_id"], key)
        contact = self.store.detail(course, self.doc)["lead"]["contact_id"]
        self.assertEqual(len(self.store.contact_opportunities(self.doc, contact)["opportunities"]), 2)
        with institute_access([]):
            self.assertEqual(len(self.store.contact_opportunities(self.doc, contact)["opportunities"]), 1)
            self.assertFalse(self.store.inbox(self.doc, 1, business="course")["conversations"])

    def test_course_import_recovers_unique_phone_by_kiwify_email_without_experts(self):
        self.lookup.side_effect = AssertionError("Course must not query Experts")
        record = {"source_id": 100, "source_contact": 100, "name": "Aluno", "title": "RegenCode", "phones": [],
                  "email": "aluno@example.test", "stage": {"name": "ALUNOS", "kind": "open"}, "value_cents": 20000,
                  "created_at": 1700000000, "updated_at": 1700000001, "reason": "phone"}
        source = lambda progress: {"source_pipeline": 13711512, "source_name": "REGENCODE", "records": [dict(record)]}
        key = prepare(self.store, self.doc, self.course["id"], background=False, source=source)["id"]
        result = commit(self.store, self.doc, self.course["id"], key, "Master")
        self.assertEqual(result["report"]["created"], 1)
        self.lookup.assert_not_called()

    def test_failed_refresh_preserves_cached_orders(self):
        with patch("crm_courses.Kiwify") as provider:
            provider.return_value.sales.side_effect = RuntimeError("offline")
            with self.assertRaises(RuntimeError):
                refresh(self.source, "victor-paranhos", "regen-code")
        self.assertEqual(self.summary(["aluno@example.test"])["paid_cents"], 20000)

    def test_course_campaign_uses_course_name_and_respects_institute_access(self):
        from crm_campaigns import audience, create, dashboard
        self.lead(self.clinic, name="Nome clinico")
        course = self.lead(name="Nome no curso")
        channel = self.store.channel({"doctor_id": self.doc, "name": "Atendimento", "phone": "5511999998888"})
        actor = {"id": 1, "nome": "Master"}
        preview = audience(self.store, {"doctor_id": self.doc, "pipeline_id": self.course["id"]}, actor)
        key = create(self.store, {"doctor_id": self.doc, "channel_id": channel, "name": "Curso", "body": "Ola {nome}", "preview_id": preview["preview_id"]}, actor)
        with self.store.db() as c:
            self.assertIn("Ola Nome", c.execute("SELECT body FROM messages WHERE campaign_id=?", (key,)).fetchone()[0])
        with institute_access([]):
            self.assertFalse(dashboard(self.store, self.doc, {})["campaigns"])
            with self.store.db() as c, self.assertRaises(PermissionError):
                self.store.row(c, "campaigns", key)
