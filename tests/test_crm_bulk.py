import json
from pathlib import Path
import tempfile
import time
import unittest

from crm_access import crm_access
from crm_bulk import apply, preview, trash
from crm_campaigns import candidates, audience
from crm_dashboard import dashboard
from crm_store import CrmStore, Conflict


class BulkLeadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(Path(self.tmp.name))
        self.store.bootstrap({"Victor Paranhos de Andrade": ["Funil A", "Funil B"], "Outra doutora": ["Funil C"]})
        self.doctor = self.store.listing()["doctor_id"]
        self.pipeline, self.other = self.store.listing(self.doctor)["pipelines"]
        self.actor = {"id": "operator-1", "nome": "Equipe"}
        self.a = self.lead("5500000000001")
        self.b = self.lead("5500000000002")
        self.c = self.lead("5500000000003", self.other)
        self.stage = self.pipeline["stages"][1]["id"]

    def lead(self, number, pipeline=None):
        p = pipeline or self.pipeline
        return self.store.lead({"doctor_id": p["doctor_id"], "pipeline_id": p["id"], "stage_id": p["stages"][0]["id"],
            "phone": number, "name": "Contato sintetico " + number[-1], "title": "Teste"}, "Equipe")

    def prepare(self, action="move", ids=None, selection=None, **changes):
        return preview(self.store, {"doctor_id": self.doctor, "pipeline_id": self.pipeline["id"], "action": action,
            "stage_id": self.stage, "selection": selection if selection is not None else {"lead_ids": ids or [self.a, self.b]}, **changes}, self.actor)

    def commit(self, batch, actor=None, **changes):
        return apply(self.store, {"id": batch["id"], "doctor_id": self.doctor, "confirm": True, **changes}, actor or self.actor)

    def test_move_is_previewed_atomic_idempotent_and_emits_normal_history(self):
        before = self.store.detail(self.a, self.doctor)["lead"]
        batch = self.prepare()
        self.assertEqual(batch["count"], 2)
        self.assertEqual(self.store.detail(self.a, self.doctor)["lead"]["revision"], before["revision"])
        result = self.commit(batch)
        self.assertEqual(result["count"], 2)
        self.assertEqual(self.commit(batch), result)
        for ident in (self.a, self.b):
            detail = self.store.detail(ident, self.doctor)
            self.assertEqual(detail["lead"]["stage_id"], self.stage)
            self.assertEqual(sum(e["kind"] == "stage" for e in detail["events"]), 1)
        self.assertEqual(self.store.listing(self.doctor)["game"]["xp"], 10)

    def test_one_stale_lead_prevents_every_change(self):
        batch = self.prepare()
        changed = self.store.detail(self.b, self.doctor)["lead"]
        self.store.lead({**changed, "title": "Editado em outra tela"}, "Outra pessoa")
        with self.assertRaises(Conflict):
            self.commit(batch)
        self.assertEqual(self.store.detail(self.a, self.doctor)["lead"]["stage_id"], self.pipeline["stages"][0]["id"])
        self.assertEqual(self.store.listing(self.doctor)["game"]["xp"], 0)

    def test_target_pipeline_revision_and_confirmation_are_checked(self):
        with self.assertRaises(ValueError):
            self.prepare(stage_id=self.other["stages"][1]["id"])
        batch = self.prepare()
        with self.assertRaises(ValueError):
            self.commit(batch, confirm=False)
        self.store.pipeline({**self.pipeline, "name": "Renomeado"})
        with self.assertRaises(Conflict):
            self.commit(batch)

    def test_snapshot_is_bound_to_actor_and_expires(self):
        batch = self.prepare()
        with self.assertRaises(LookupError):
            self.commit(batch, actor={"id": "other", "nome": "Outra pessoa"})
        with self.store.db() as c:
            c.execute("UPDATE lead_bulk_batches SET expires_at=0 WHERE id=?", (batch["id"],))
        with self.assertRaises(Conflict):
            self.commit(batch)

    def test_scope_is_checked_at_preview_and_commit_even_after_access_revoked(self):
        with crm_access({self.doctor: [self.other["id"]]}, self.doctor), self.assertRaises(PermissionError):
            self.prepare()
        batch = self.prepare()
        with crm_access({self.doctor: [self.other["id"]]}, self.doctor), self.assertRaises(PermissionError):
            self.commit(batch)
        self.commit(batch)
        with crm_access({}, ""), self.assertRaises(PermissionError):
            self.commit(batch)

    def test_select_all_uses_funnel_and_filters_and_includes_opted_out_contacts(self):
        self.store.activity({"id": self.b, "doctor_id": self.doctor, "kind": "optout", "body": "SAIR"}, "Equipe")
        batch = self.prepare(selection={"filters": {}, "exclude_lead_ids": [self.a]})
        self.assertEqual(batch["count"], 1)
        self.assertEqual(batch["sample"][0]["id"], self.b)
        self.commit(batch)
        self.assertTrue(self.store.detail(self.b, self.doctor)["lead"]["opted_out"])
        self.assertEqual(self.store.detail(self.c, self.doctor)["lead"]["stage_id"], self.other["stages"][0]["id"])

    def test_delete_preserves_contact_history_but_hides_opportunity_and_metrics(self):
        contact = self.store.detail(self.a, self.doctor)["lead"]["contact_id"]
        batch = self.prepare("delete", [self.a])
        self.commit(batch)
        with self.assertRaises(LookupError):
            self.store.detail(self.a, self.doctor)
        data = self.store.listing(self.doctor, kanban_pipeline=self.pipeline["id"])
        self.assertEqual(data["kanban"]["summary"]["total"], 1)
        self.assertEqual(data["total_leads"], 2)
        self.assertEqual(candidates(self.store, {"doctor_id": self.doctor, "pipeline_id": self.pipeline["id"]})["selected"], 1)
        self.assertEqual(next(f["total"] for f in dashboard(self.store, self.doctor)["funnels"] if f["id"] == self.pipeline["id"]), 1)
        with self.store.db() as c:
            self.assertTrue(c.execute("SELECT 1 FROM contacts WHERE id=?", (contact,)).fetchone())
            self.assertTrue(c.execute("SELECT 1 FROM events WHERE lead_id=? AND kind='created'", (self.a,)).fetchone())
            self.assertEqual(c.execute("SELECT deleted_by FROM leads WHERE id=?", (self.a,)).fetchone()[0], "Equipe")

    def test_restore_is_explicit_and_does_not_resume_bots_or_award_points(self):
        original = self.store.detail(self.a, self.doctor)["lead"]
        self.commit(self.prepare("delete", [self.a]))
        data = trash(self.store, self.doctor, self.pipeline["id"])
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["id"], self.a)
        with self.assertRaises(ValueError):
            self.lead("5500000000001")
        with self.store.db() as c:
            events = c.execute("SELECT count(*) FROM bot_events").fetchone()[0]
        batch = self.prepare("restore", [self.a])
        self.commit(batch)
        restored = self.store.detail(self.a, self.doctor)["lead"]
        self.assertEqual(restored["stage_id"], original["stage_id"])
        self.assertEqual(restored["revision"], original["revision"] + 2)
        self.assertEqual(trash(self.store, self.doctor, self.pipeline["id"])["total"], 0)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM bot_events").fetchone()[0], events)

    def test_empty_or_already_target_selection_does_not_create_batch(self):
        with self.assertRaises(ValueError):
            self.prepare(selection={"lead_ids": []})
        with self.assertRaises(ValueError):
            self.prepare(stage_id=self.pipeline["stages"][0]["id"])
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM lead_bulk_batches").fetchone()[0], 0)

    def test_deleted_contact_conversation_is_preserved(self):
        channel = self.store.channel({"doctor_id": self.doctor, "name": "Teste", "phone": "5500000000004"})
        with self.store.db() as c:
            instance = self.store.row(c, "channels", channel)["instance"]
        self.store.receive(instance, {"key": {"remoteJid": "5500000000001@s.whatsapp.net", "id": "message"},
                                    "message": {"conversation": "Mensagem preservada"}}, "messages.upsert")
        contact = self.store.detail(self.a, self.doctor)["lead"]["contact_id"]
        self.commit(self.prepare("delete", [self.a]))
        conversation = self.store.conversation(self.doctor, channel, contact)
        self.assertEqual(conversation["messages"][0]["body"], "Mensagem preservada")
        self.assertEqual(self.store.contact_opportunities(self.doctor, contact)["opportunities"], [])

    def test_deleted_lead_does_not_reappear_on_new_whatsapp_message(self):
        channel = self.store.channel({"doctor_id": self.doctor, "name": "Teste", "phone": "5500000000004"})
        with self.store.db() as c:
            instance = self.store.row(c, "channels", channel)["instance"]
        self.commit(self.prepare("delete", [self.a]))
        self.store.receive(instance, {"key": {"remoteJid": "5500000000001@s.whatsapp.net", "id": "new-message"},
                                    "message": {"conversation": "Nova mensagem"}}, "messages.upsert")
        self.assertEqual(trash(self.store, self.doctor, self.pipeline["id"])["total"], 1)
        self.assertNotIn(self.a, [l["id"] for l in self.store.listing(self.doctor)["leads"]])

    def test_delete_cancels_only_corresponding_queued_campaign_recipient(self):
        channel = self.store.channel({"doctor_id": self.doctor, "name": "Teste", "phone": "5500000000004"})
        frozen = audience(self.store, {"doctor_id": self.doctor, "pipeline_id": self.pipeline["id"], "lead_ids": [self.a, self.b]}, self.actor)
        campaign = self.store.campaign({"doctor_id": self.doctor, "channel_id": channel, "name": "Teste", "body": "Mensagem",
            "lead_ids": [self.a, self.b], "_audience_id": frozen["preview_id"]}, self.actor["nome"], self.actor["id"])
        self.commit(self.prepare("delete", [self.a]))
        with self.store.db() as c:
            rows = c.execute("SELECT status FROM messages WHERE campaign_id=? ORDER BY status", (campaign,)).fetchall()
            self.assertEqual([r[0] for r in rows], ["queued", "skipped"])

