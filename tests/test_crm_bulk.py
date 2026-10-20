import json
from pathlib import Path
import tempfile
import time
import unittest

from crm_access import crm_access, institute_access
from crm_bulk import apply, destinations, preview, trash
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

    def transfer(self, target=None, ids=None, **changes):
        target = target or self.other
        return self.prepare("transfer", ids or [self.a], target_doctor_id=target["doctor_id"], target_pipeline_id=target["id"],
                            stage_id=target["stages"][1]["id"], **changes)

    def foreign_pipeline(self):
        doctor = next(d["id"] for d in self.store.listing()["doctors"] if d["id"] != self.doctor)
        return self.store.listing(doctor)["pipelines"][0]

    def test_transfer_to_another_funnel_reuses_lead_and_preserves_identity_notes_and_utm(self):
        original = self.store.detail(self.a, self.doctor)["lead"]
        self.store.activity({"id": self.a, "doctor_id": self.doctor, "kind": "note", "body": "Nota preservada"}, "Equipe")
        with self.store.db() as c:
            c.execute("UPDATE leads SET value_cents=12345,email='teste@example.test',next_action='2026-10-25' WHERE id=?", (self.a,))
            c.execute("INSERT INTO lead_attributions VALUES(?,?,?,?,?,?)", ("utm", self.a, "form", "Origem", 1, '{"utm":{"utm_source":"teste"}}'))
            contacts = [tuple(r) for r in c.execute("SELECT id,name,phone,opted_out,experts_uuid FROM contacts")]
            messages = [tuple(r) for r in c.execute("SELECT * FROM messages")]
            events = c.execute("SELECT count(*) FROM bot_events").fetchone()[0]
        batch = self.transfer()
        result = self.commit(batch)
        self.assertEqual(self.commit(batch), result)
        moved = self.store.detail(self.a, self.doctor)
        self.assertEqual((moved["lead"]["pipeline_id"], moved["lead"]["stage_id"]), (self.other["id"], self.other["stages"][1]["id"]))
        self.assertEqual(moved["lead"]["contact_id"], original["contact_id"])
        self.assertEqual((moved["lead"]["value_cents"], moved["lead"]["email"], moved["lead"]["next_action"]), (12345, "teste@example.test", "2026-10-25"))
        self.assertTrue(any(e["body"] == "Nota preservada" for e in moved["events"]))
        self.assertEqual(moved["attribution"]["first"]["data"]["utm"]["utm_source"], "teste")
        with self.store.db() as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT id,name,phone,opted_out,experts_uuid FROM contacts")], contacts)
            self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM messages")], messages)
            self.assertEqual(c.execute("SELECT count(*) FROM leads").fetchone()[0], 3)
            self.assertEqual(c.execute("SELECT count(*) FROM xp").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM bot_events").fetchone()[0], events)
            self.assertEqual(c.execute("SELECT count(*) FROM lead_transfers").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_cross_doctor_transfer_preserves_source_conversation_and_does_not_recreate_wrong_lead(self):
        target = self.foreign_pipeline()
        contact = self.store.detail(self.a, self.doctor)["lead"]["contact_id"]
        channel = self.store.channel({"doctor_id": self.doctor, "name": "Origem", "phone": "5500000000004"})
        with self.store.db() as c:
            instance = self.store.row(c, "channels", channel)["instance"]
            c.execute("UPDATE contacts SET opted_out=1 WHERE id=?", (contact,))
        def incoming(ident):
            return self.store.receive(instance, {"key": {"remoteJid": "5500000000001@s.whatsapp.net", "id": ident},
                "message": {"conversation": "Mensagem preservada"}}, "messages.upsert")
        incoming("before")
        self.commit(self.transfer(target))
        moved = self.store.detail(self.a, target["doctor_id"])["lead"]
        self.assertEqual(moved["id"], self.a)
        self.assertEqual(moved["contact_id"], contact)
        self.assertTrue(moved["opted_out"])
        self.assertEqual(len(self.store.conversation(self.doctor, channel, contact)["messages"]), 1)
        with crm_access({self.doctor: [self.pipeline["id"]]}, self.doctor):
            self.assertEqual(len(self.store.conversation(self.doctor, channel, contact)["messages"]), 1)
        with crm_access({self.doctor: [self.other["id"]]}, self.doctor), self.assertRaises(PermissionError):
            self.store.conversation(self.doctor, channel, contact)
        with self.assertRaises(ValueError):
            self.store.conversation(target["doctor_id"], channel, contact)
        incoming("after")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM leads WHERE contact_id=?", (contact,)).fetchone()[0], 1)
            self.assertEqual(self.store.row(c, "channels", channel)["doctor_id"], self.doctor)
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE channel_id=?", (channel,)).fetchone()[0], 2)
        # A deliberate new opportunity at the source remains possible.
        self.lead("5500000000001")
        incoming("again")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM leads WHERE contact_id=?", (contact,)).fetchone()[0], 2)

    def test_transfer_rejects_duplicates_including_trash_and_rechecks_after_preview(self):
        duplicate = self.lead("5500000000001", self.other)
        with self.assertRaisesRegex(Conflict, "duplicidade"):
            self.transfer()
        with self.store.db() as c:
            c.execute("UPDATE leads SET deleted_at=1 WHERE id=?", (duplicate,))
        with self.assertRaises(Conflict):
            self.transfer()
        batch = self.transfer(ids=[self.b])
        self.lead("5500000000002", self.other)
        with self.assertRaises(Conflict):
            self.commit(batch)
        self.assertEqual(self.store.detail(self.b, self.doctor)["lead"]["pipeline_id"], self.pipeline["id"])

    def test_transfer_rechecks_both_scopes_and_lists_only_allowed_destinations(self):
        target = self.foreign_pipeline()
        scope = {self.doctor: [self.pipeline["id"]], target["doctor_id"]: [target["id"]]}
        with crm_access(scope, self.doctor):
            options = destinations(self.store, self.doctor)
            self.assertEqual({p["id"] for p in options["pipelines"]}, {self.pipeline["id"], target["id"]})
            batch = self.transfer(target)
        with crm_access({self.doctor: [self.pipeline["id"]]}, self.doctor), self.assertRaises(PermissionError):
            self.commit(batch)
        with crm_access({target["doctor_id"]: [target["id"]]}), self.assertRaises(PermissionError):
            self.commit(batch)
        with crm_access(scope, self.doctor):
            self.commit(batch)
        with crm_access({self.doctor: None}, self.doctor), self.assertRaises(PermissionError):
            self.commit(batch)

    def test_transfer_rejects_wrong_stage_same_funnel_stale_rows_and_changed_target(self):
        with self.assertRaises(ValueError):
            self.prepare("transfer", target_doctor_id=self.doctor, target_pipeline_id=self.pipeline["id"])
        with self.assertRaises(ValueError):
            self.prepare("transfer", target_doctor_id=self.doctor, target_pipeline_id=self.other["id"], stage_id=self.stage)
        batch = self.transfer(ids=[self.a, self.b])
        with self.store.db() as c:
            c.execute("UPDATE leads SET revision=revision+1 WHERE id=?", (self.b,))
        with self.assertRaises(Conflict):
            self.commit(batch)
        batch = self.transfer()
        self.store.pipeline({**self.other, "name": "Destino renomeado"})
        with self.assertRaises(Conflict):
            self.commit(batch)
        self.assertEqual(self.store.detail(self.a, self.doctor)["lead"]["pipeline_id"], self.pipeline["id"])

    def test_transfer_stops_only_old_automations_and_pending_selected_recipients(self):
        from crm_bots import BotStore
        channel = self.store.channel({"doctor_id": self.doctor, "name": "Origem", "phone": "5500000000004"})
        frozen = audience(self.store, {"doctor_id": self.doctor, "pipeline_id": self.pipeline["id"], "lead_ids": [self.a, self.b]}, self.actor)
        campaign = self.store.campaign({"doctor_id": self.doctor, "channel_id": channel, "name": "Teste", "body": "Mensagem",
            "lead_ids": [self.a, self.b], "_audience_id": frozen["preview_id"]}, self.actor["nome"], self.actor["id"])
        bots = BotStore(self.store)
        bot = bots.save({"doctor_id": self.doctor, "pipeline_id": self.pipeline["id"], "name": "Origem", "definition": {
            "trigger": {"kind": "manual"}, "nodes": [{"id": "start", "kind": "start", "next": {"next": "end"}}, {"id": "end", "kind": "end"}]}}, self.actor["id"])
        bots.publish(bot["id"], self.doctor, bot["revision"], True, self.actor["id"])
        run = bots.start(bot["id"], self.doctor, self.a, "test")
        self.commit(self.transfer())
        with self.store.db() as c:
            statuses = [r[0] for r in c.execute("SELECT status FROM messages WHERE campaign_id=? ORDER BY status", (campaign,))]
            self.assertEqual(statuses, ["queued", "skipped"])
            self.assertEqual(c.execute("SELECT status FROM bot_runs WHERE id=?", (run,)).fetchone()[0], "cancelled")
            self.assertEqual(c.execute("SELECT status FROM crm_bots WHERE id=?", (bot["id"],)).fetchone()[0], "active")
            self.assertFalse(c.execute("SELECT 1 FROM bot_events WHERE lead_id=? AND processed=0", (self.a,)).fetchone())

    def test_transfer_blocks_in_flight_automations_at_commit(self):
        channel = self.store.channel({"doctor_id": self.doctor, "name": "Origem", "phone": "5500000000004"})
        campaign = self.store.campaign({"doctor_id": self.doctor, "channel_id": channel, "name": "Teste", "body": "Mensagem", "lead_ids": [self.a]}, self.actor["nome"])
        batch = self.transfer()
        for status in ("sending", "unknown"):
            with self.store.db() as c:
                c.execute("UPDATE messages SET status=? WHERE campaign_id=?", (status, campaign))
            with self.assertRaises(Conflict):
                self.commit(batch)
            with self.assertRaises(Conflict):
                self.transfer()

    def test_transfer_preserves_course_separation_and_institute_permissions(self):
        with self.store.db() as c:
            c.execute("UPDATE pipelines SET institute_key='victor-paranhos',course_key='regen-code' WHERE id=?", (self.other["id"],))
        with institute_access([]):
            self.assertNotIn(self.other["id"], [p["id"] for p in destinations(self.store, self.doctor)["pipelines"]])
            with self.assertRaises(PermissionError):
                self.transfer()
        with institute_access(["victor-paranhos"]):
            self.commit(self.transfer())
            moved = self.store.detail(self.a, self.doctor)["lead"]
            self.assertEqual(moved["course_key"], "regen-code")
            self.assertEqual(moved["source_name"], moved["name"])
            self.assertEqual(moved["course_data"]["state"], "unavailable")

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

