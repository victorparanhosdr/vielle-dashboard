from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

from crm_channels import lifecycle, removal_options, transfer, transfer_options, update
from crm_access import crm_access
from crm_store import CrmStore, Conflict


class ChannelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(Path(self.tmp.name))
        self.store.bootstrap({"Victor Paranhos de Andrade": ["A", "B", "RegenCode"], "Outra doutora": ["Outro"]})
        self.doc = self.store.listing()["doctor_id"]
        self.a, self.b, self.course = self.store.listing(self.doc)["pipelines"]
        self.channel = self.store.channel({"doctor_id": self.doc, "phone": "5500000000001", "name": "Nome inicial"})

    def channel_row(self):
        with self.store.db() as c:
            return self.store.row(c, "channels", self.channel)

    def configure(self, **extra):
        row = self.channel_row()
        return update(self.store, {"id": row["id"], "doctor_id": self.doc, "config_revision": row["config_revision"],
            "name": "Comercial WhatsApp", "inbound_pipeline_id": self.b["id"], "inbound_stage_id": self.b["stages"][1]["id"], **extra})

    def incoming(self, number="5500000000002", ident="message"):
        return self.store.receive(self.channel_row()["instance"], {"key": {"remoteJid": number + "@s.whatsapp.net", "id": ident},
            "pushName": "Contato sintetico", "message": {"conversation": "Ola"}}, "messages.upsert")

    def test_rename_and_routing_preserve_connection_identity_and_state(self):
        before = self.channel_row()
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open',next_send_at=123 WHERE id=?", (self.channel,))
        self.configure()
        after = self.channel_row()
        self.assertEqual(after["name"], "Comercial WhatsApp")
        self.assertEqual(after["config_revision"], 2)
        for field in ("phone", "instance", "daily_limit", "doctor_id"):
            self.assertEqual(after[field], before[field])
        self.assertEqual((after["state"], after["next_send_at"]), ("open", 123))

    def test_new_lead_uses_configured_funnel_and_stage(self):
        self.configure()
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        self.assertEqual((lead["pipeline_id"], lead["stage_id"]), (self.b["id"], self.b["stages"][1]["id"]))

    def test_existing_lead_is_not_moved_or_duplicated(self):
        old = self.store.lead({"doctor_id": self.doc, "pipeline_id": self.a["id"], "stage_id": self.a["stages"][2]["id"],
            "phone": "5500000000002", "name": "Ja cadastrado", "title": "Existente"}, "Equipe")
        self.configure()
        self.incoming()
        rows = self.store.listing(self.doc)["leads"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["id"], rows[0]["stage_id"]), (old, self.a["stages"][2]["id"]))

    def test_course_routing_does_not_search_or_require_clinical_record(self):
        self.configure(inbound_pipeline_id=self.course["id"], inbound_stage_id=self.course["stages"][0]["id"])
        lookup = Mock(side_effect=AssertionError("No clinical lookup for course leads"))
        self.store.patient_lookup = lookup
        self.incoming()
        lookup.assert_not_called()
        with self.store.db() as c:
            lead = c.execute("SELECT * FROM leads").fetchone()
            self.assertEqual(lead["pipeline_id"], self.course["id"])
            self.assertEqual(lead["source_name"], "Contato sintetico")

    def test_invalid_destinations_and_stale_updates_do_not_change_channel(self):
        other = next(d["id"] for d in self.store.listing()["doctors"] if d["id"] != self.doc)
        foreign = self.store.listing(other)["pipelines"][0]
        for changes in ({"inbound_pipeline_id": foreign["id"], "inbound_stage_id": foreign["stages"][0]["id"]},
                        {"inbound_stage_id": self.a["stages"][0]["id"]},
                        {"inbound_stage_id": self.b["stages"][-1]["id"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.configure(**changes)
            self.assertEqual(self.channel_row()["name"], "Nome inicial")
        self.configure()
        with self.assertRaises(Conflict):
            self.configure(config_revision=1)

    def test_default_route_remains_compatible_and_can_be_restored(self):
        self.configure()
        self.configure(inbound_pipeline_id="", inbound_stage_id="")
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        self.assertEqual(lead["pipeline_id"], self.a["id"])

    def test_destination_stage_cannot_be_removed_or_closed_while_configured(self):
        self.configure()
        route = self.b["stages"][1]["id"]
        with self.assertRaises(ValueError):
            self.store.pipeline({**self.b, "stages": [s for s in self.b["stages"] if s["id"] != route]})
        stages = [dict(s) for s in self.b["stages"]]
        stages[1]["kind"] = "won"
        with self.assertRaises(ValueError):
            self.store.pipeline({**self.b, "stages": stages})

    def transfer_payload(self, **changes):
        other = next(d["id"] for d in self.store.listing()["doctors"] if d["id"] != self.doc)
        pipeline = self.store.listing(other)["pipelines"][0]
        return {"id": self.channel, "doctor_id": self.doc, "target_doctor_id": other,
                "config_revision": self.channel_row()["config_revision"], "confirm": True,
                "inbound_pipeline_id": pipeline["id"], "inbound_stage_id": pipeline["stages"][1]["id"], **changes}

    def remove_payload(self, **changes):
        return {"id": self.channel, "doctor_id": self.doc, "config_revision": self.channel_row()["config_revision"],
                "confirm": True, **changes}

    def test_removal_preserves_all_records_attachments_and_history_access(self):
        from crm_media import path_for
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        media_id = "f" * 32
        file = path_for(self.store, media_id)
        file.parent.mkdir(exist_ok=True)
        file.write_bytes(b"historical attachment")
        tables = ("contacts", "leads", "messages", "message_media", "events", "conversation_reads", "conversation_resolutions")
        with self.store.db() as c:
            message = c.execute("SELECT id FROM messages LIMIT 1").fetchone()[0]
            c.execute("""INSERT INTO message_media(id,doctor_id,source_channel_id,contact_id,user_id,upload_request,name,mime,kind,size,sha256,created_at,message_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (media_id, self.doc, self.channel, lead["contact_id"], "operator", "upload", "teste.pdf", "application/pdf", "document", 21, "hash", 1, message))
            c.execute("UPDATE messages SET media_id=? WHERE id=?", (media_id, message))
            c.execute("INSERT INTO conversation_reads VALUES(?,?,?,?)", (self.channel, lead["contact_id"], "operator", 1))
            snapshot = {table: [tuple(r) for r in c.execute("SELECT * FROM " + table)] for table in tables}
        before = self.channel_row()
        self.assertEqual(removal_options(self.store, self.channel, self.doc)["impact"]["messages"], 1)
        result = lifecycle(self.store, self.remove_payload(), "master", "Master")
        after = self.channel_row()
        self.assertTrue(after["archived_at"])
        self.assertEqual((after["state"], after["archived_by"]), ("archived", "master"))
        for key in ("id", "phone", "instance", "doctor_id", "inbound_pipeline_id", "inbound_stage_id"):
            self.assertEqual(before[key], after[key])
        with self.store.db() as c:
            self.assertEqual({table: [tuple(r) for r in c.execute("SELECT * FROM " + table)] for table in tables}, snapshot)
            audit = dict(c.execute("SELECT * FROM channel_lifecycle").fetchone())
            self.assertEqual((audit["id"], audit["action"], audit["actor_id"]), (result["event_id"], "remove", "master"))
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(file.read_bytes(), b"historical attachment")
        self.assertEqual(self.store.inbox(self.doc, "operator")["conversations"], [])
        self.assertEqual(len(self.store.inbox(self.doc, "operator", self.channel)["conversations"]), 1)
        conversation = self.store.conversation(self.doc, self.channel, lead["contact_id"])
        self.assertFalse(conversation["reply_allowed"])
        self.assertEqual(conversation["messages"][0]["media"]["id"], media_id)
        self.assertEqual(self.store.detail(lead["id"], self.doc)["messages"][0]["id"], message)
        self.assertEqual(len(self.store.listing(self.doc)["leads"]), 1)
        self.assertEqual(self.store.listing(self.doc)["channels"][0]["id"], self.channel)

    def test_removed_connection_ignores_webhooks_and_cannot_be_reactivated_by_provider(self):
        self.incoming()
        lifecycle(self.store, self.remove_payload(), "master", "Master")
        self.assertFalse(self.incoming(number="5500000000003", ident="ignored"))
        self.assertFalse(self.store.receive(self.channel_row()["instance"], {"state": "open"}, "connection.update"))
        self.assertEqual(self.channel_row()["state"], "archived")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM leads").fetchone()[0], 1)

    def test_removal_cancels_pending_sends_and_restore_does_not_restart_them(self):
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        campaign = self.store.campaign({"doctor_id": self.doc, "channel_id": self.channel, "name": "Pendente",
            "body": "Teste", "lead_ids": [lead["id"]]}, "Master")
        lifecycle(self.store, self.remove_payload(), "master", "Master")
        with self.store.db() as c:
            self.assertEqual(self.store.row(c, "campaigns", campaign)["status"], "paused")
            self.assertEqual(c.execute("SELECT status FROM messages WHERE campaign_id=?", (campaign,)).fetchone()[0], "cancelled")
        lifecycle(self.store, self.remove_payload(), "master", "Master", restore=True)
        self.assertEqual((self.channel_row()["archived_at"], self.channel_row()["state"]), (0, "close"))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT action FROM channel_lifecycle ORDER BY created_at").fetchall()[1][0], "restore")
            self.assertEqual(self.store.row(c, "campaigns", campaign)["status"], "paused")
            self.assertEqual(c.execute("SELECT status FROM messages WHERE campaign_id=?", (campaign,)).fetchone()[0], "cancelled")
        self.assertIsNone(self.store.claim())
        self.incoming(ident="restored")
        self.assertEqual(len(self.store.listing(self.doc)["leads"]), 1)

    def test_remove_requires_confirmation_current_revision_and_matching_owner(self):
        other = next(d["id"] for d in self.store.listing()["doctors"] if d["id"] != self.doc)
        for change in ({"confirm": False}, {"confirm": "true"}, {"config_revision": 0}, {"config_revision": True}, {"doctor_id": other}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                lifecycle(self.store, self.remove_payload(**change), "master", "Master")
            self.assertFalse(self.channel_row()["archived_at"])
        with self.assertRaises(ValueError):
            lifecycle(self.store, self.remove_payload(), "master", "Master", restore=True)
        stale = self.remove_payload()
        lifecycle(self.store, stale, "master", "Master")
        with self.assertRaises(Conflict):
            lifecycle(self.store, stale, "master", "Master", restore=True)

    def test_sending_and_unknown_cross_channel_replies_block_removal(self):
        self.incoming()
        other = self.store.channel({"doctor_id": self.doc, "phone": "5500000000004", "name": "Outro"})
        with self.store.db() as c:
            message = c.execute("SELECT id FROM messages").fetchone()[0]
            c.execute("UPDATE messages SET channel_id=? WHERE id=?", (other, message))
            c.execute("INSERT INTO reply_requests(request_id,message_id,user_id,source_channel_id) VALUES(?,?,?,?)", ("cross", message, "operator", self.channel))
        for status in ("sending", "unknown"):
            with self.store.db() as c:
                c.execute("UPDATE messages SET status=? WHERE id=?", (status, message))
            with self.subTest(status=status), self.assertRaises(Conflict):
                lifecycle(self.store, self.remove_payload(), "master", "Master")
            self.assertFalse(self.channel_row()["archived_at"])

    def test_removed_connection_rejects_edits_transfer_new_sends_uploads_and_duplicates(self):
        from crm_media import upload
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        lifecycle(self.store, self.remove_payload(), "master", "Master")
        for operation in (
            lambda: self.configure(),
            lambda: transfer(self.store, self.transfer_payload(), "master", "Master"),
            lambda: self.store.channel({"doctor_id": self.doc, "name": "Duplicado", "phone": self.channel_row()["phone"]}),
            lambda: self.store.reply({"doctor_id": self.doc, "channel_id": self.channel, "contact_id": lead["contact_id"], "body": "Ola", "request_id": "no"}, "operator", True, sender_name="Master"),
            lambda: self.store.campaign({"doctor_id": self.doc, "channel_id": self.channel, "name": "Teste", "body": "Ola", "lead_ids": [lead["id"]]}, "Master"),
            lambda: upload(self.store, self.doc, self.channel, lead["contact_id"], "operator", "no", "teste.pdf", "document", b"%PDF-1.4\n%%EOF"),
        ):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()

    def test_archived_source_cannot_send_via_another_number_and_cross_replies_are_cancelled(self):
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        other = self.store.channel({"doctor_id": self.doc, "phone": "5500000000004", "name": "Outro"})
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open'")
        payload = {"doctor_id": self.doc, "channel_id": other, "source_channel_id": self.channel,
                   "contact_id": lead["contact_id"], "body": "Teste", "request_id": "cross"}
        pending = self.store.reply(payload, "operator", True, sender_name="Master")
        lifecycle(self.store, self.remove_payload(), "master", "Master")
        with self.store.db() as c:
            self.assertEqual(self.store.row(c, "messages", pending)["status"], "cancelled")
            self.assertEqual(self.store.row(c, "channels", other)["state"], "open")
        with self.assertRaisesRegex(ValueError, "removido"):
            self.store.reply({**payload, "request_id": "blocked"}, "operator", True, sender_name="Master")
        with crm_access({self.doc: {self.b["id"]}}), self.assertRaises(PermissionError):
            self.store.conversation(self.doc, self.channel, lead["contact_id"])

    def test_removal_pauses_related_bot_and_blocks_republishing_until_restored(self):
        from crm_bots import BotStore
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        bots = BotStore(self.store)
        definition = {"trigger": {"kind": "manual"}, "nodes": [
            {"id": "start", "kind": "start", "next": {"next": "send"}},
            {"id": "send", "kind": "message", "data": {"body": "Ola", "channel_id": self.channel}, "next": {"next": "end"}},
            {"id": "end", "kind": "end"}]}
        bot = bots.save({"doctor_id": self.doc, "pipeline_id": lead["pipeline_id"], "name": "Relacionado", "definition": definition}, "master")
        bots.publish(bot["id"], self.doc, bot["revision"], True, "master")
        run = bots.start(bot["id"], self.doc, lead["id"], "test")
        lifecycle(self.store, self.remove_payload(), "master", "Master")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT status FROM crm_bots WHERE id=?", (bot["id"],)).fetchone()[0], "paused")
            self.assertEqual(c.execute("SELECT status FROM bot_runs WHERE id=?", (run,)).fetchone()[0], "cancelled")
            self.assertFalse(c.execute("SELECT 1 FROM bot_events WHERE channel_id=? AND processed=0", (self.channel,)).fetchone())
            revision = c.execute("SELECT revision FROM crm_bots WHERE id=?", (bot["id"],)).fetchone()[0]
        with self.assertRaises(ValueError):
            bots.publish(bot["id"], self.doc, revision, True, "master")

    def test_transfer_preserves_session_history_identity_and_old_opportunities(self):
        self.configure()
        self.incoming()
        original = self.store.listing(self.doc)["leads"][0]
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open',next_send_at=123 WHERE id=?", (self.channel,))
            c.execute("UPDATE contacts SET opted_out=1 WHERE id=?", (original["contact_id"],))
            c.execute("INSERT INTO conversation_reads VALUES(?,?,?,?)", (self.channel, original["contact_id"], "operator", 1))
            before_messages = [tuple(r) for r in c.execute("SELECT * FROM messages")]
            before_lead = dict(c.execute("SELECT * FROM leads WHERE id=?", (original["id"],)).fetchone())
        before = self.channel_row()
        options = transfer_options(self.store, self.channel, self.doc)
        self.assertEqual(options["impact"]["conversations"], 1)
        self.assertNotIn(self.doc, [d["id"] for d in options["doctors"]])
        payload = self.transfer_payload()
        result = transfer(self.store, payload, "master", "Master sintetico")
        after = self.channel_row()
        self.assertEqual(after["doctor_id"], payload["target_doctor_id"])
        self.assertEqual(after["inbound_stage_id"], payload["inbound_stage_id"])
        self.assertEqual(after["config_revision"], before["config_revision"] + 1)
        for key in ("id", "phone", "instance", "state", "next_send_at", "name", "daily_limit"):
            self.assertEqual(after[key], before[key])
        with self.store.db() as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM messages")], before_messages)
            self.assertEqual(dict(c.execute("SELECT * FROM leads WHERE id=?", (original["id"],)).fetchone()), before_lead)
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT opted_out FROM contacts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM conversation_reads").fetchone()[0], 1)
            audit = c.execute("SELECT * FROM channel_transfers").fetchone()
            self.assertEqual(audit["id"], result["transfer_id"])
            self.assertEqual((audit["from_doctor_id"], audit["to_doctor_id"], audit["actor_id"]), (self.doc, after["doctor_id"], "master"))
            self.assertEqual(json.loads(audit["before_config"])["doctor_id"], self.doc)
            self.assertEqual(json.loads(audit["after_config"])["inbound_pipeline_id"], payload["inbound_pipeline_id"])
        self.assertEqual(self.store.listing(self.doc)["channels"], [])
        self.assertEqual(len(self.store.inbox(after["doctor_id"], "master")["conversations"]), 1)
        with self.assertRaises(ValueError):
            self.store.conversation(self.doc, self.channel, original["contact_id"])
        with crm_access({self.doc: None}), self.assertRaises(PermissionError):
            self.store.conversation(after["doctor_id"], self.channel, original["contact_id"])

    def test_incoming_after_transfer_uses_new_funnel_and_existing_contact(self):
        self.incoming()
        payload = self.transfer_payload()
        transfer(self.store, payload, "master", "Master")
        self.incoming(ident="after-transfer")
        target = self.store.listing(payload["target_doctor_id"])["leads"]
        self.assertEqual(len(target), 1)
        self.assertEqual((target[0]["pipeline_id"], target[0]["stage_id"]), (payload["inbound_pipeline_id"], payload["inbound_stage_id"]))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM leads").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 2)
        self.incoming(ident="again")
        self.assertEqual(len(self.store.listing(payload["target_doctor_id"])["leads"]), 1)

    def test_invalid_transfers_do_not_modify_anything(self):
        other_pipeline = self.transfer_payload()
        for changes in ({"confirm": False}, {"confirm": "true"}, {"target_doctor_id": self.doc},
                        {"target_doctor_id": "missing"}, {"inbound_pipeline_id": ""},
                        {"inbound_pipeline_id": self.a["id"], "inbound_stage_id": self.a["stages"][0]["id"]},
                        {"inbound_stage_id": self.a["stages"][0]["id"]}, {"config_revision": True},
                        {"config_revision": 0}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, LookupError)):
                transfer(self.store, {**other_pipeline, **changes}, "master", "Master")
            self.assertEqual(self.channel_row()["doctor_id"], self.doc)
            with self.store.db() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM channel_transfers").fetchone()[0], 0)

    def test_sending_unknown_and_cross_channel_replies_block_transfer(self):
        self.incoming()
        with self.store.db() as c:
            message = c.execute("SELECT id FROM messages").fetchone()[0]
        for status in ("sending", "unknown"):
            with self.store.db() as c:
                c.execute("UPDATE messages SET status=? WHERE id=?", (status, message))
            with self.subTest(status=status), self.assertRaises(Conflict):
                transfer(self.store, self.transfer_payload(), "master", "Master")
        another = self.store.channel({"doctor_id": self.doc, "phone": "5500000000004", "name": "Outra conexao"})
        with self.store.db() as c:
            c.execute("UPDATE messages SET channel_id=? WHERE id=?", (another, message))
            c.execute("INSERT INTO reply_requests(request_id,message_id,user_id,source_channel_id) VALUES(?,?,?,?)", ("cross", message, "operator", self.channel))
        with self.assertRaises(Conflict):
            transfer(self.store, self.transfer_payload(), "master", "Master")
        self.assertEqual(self.channel_row()["doctor_id"], self.doc)

    def test_old_campaign_is_paused_and_cannot_restart_after_transfer(self):
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        campaign = self.store.campaign({"doctor_id": self.doc, "channel_id": self.channel, "name": "Campanha sintetica",
            "body": "Mensagem sintetica", "lead_ids": [lead["id"]], "start_at": "2026-10-19T09:00"}, "Master")
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open' WHERE id=?", (self.channel,))
            c.execute("UPDATE campaigns SET status='running' WHERE id=?", (campaign,))
        transfer(self.store, self.transfer_payload(), "master", "Master")
        with self.store.db() as c:
            self.assertEqual(self.store.row(c, "campaigns", campaign)["status"], "paused")
            self.assertEqual(c.execute("SELECT status FROM messages WHERE campaign_id=?", (campaign,)).fetchone()[0], "cancelled")
        with self.assertRaises(ValueError):
            self.store.campaign_status(campaign, self.doc, "start", True)
        self.assertIsNone(self.store.claim())

    def test_related_bots_and_pending_events_stop_without_stopping_unrelated_bots(self):
        from crm_bots import BotStore
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        bots = BotStore(self.store)
        definition = {"trigger": {"kind": "manual"}, "nodes": [
            {"id": "start", "kind": "start", "next": {"next": "send"}},
            {"id": "send", "kind": "message", "data": {"body": "Ola", "channel_id": self.channel}, "next": {"next": "end"}},
            {"id": "end", "kind": "end"}]}
        bot = bots.save({"doctor_id": self.doc, "pipeline_id": lead["pipeline_id"], "name": "Relacionado", "definition": definition}, "master")
        bots.publish(bot["id"], self.doc, bot["revision"], True, "master")
        run = bots.start(bot["id"], self.doc, lead["id"], "test")
        other = bots.save({"doctor_id": self.doc, "pipeline_id": lead["pipeline_id"], "name": "Outro robo", "definition": {
            "trigger": {"kind": "manual"}, "nodes": [{"id": "start", "kind": "start", "next": {"next": "end"}}, {"id": "end", "kind": "end"}]}}, "master")
        bots.publish(other["id"], self.doc, other["revision"], True, "master")
        transfer(self.store, self.transfer_payload(), "master", "Master")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT status FROM crm_bots WHERE id=?", (bot["id"],)).fetchone()[0], "paused")
            self.assertEqual(c.execute("SELECT status FROM bot_runs WHERE id=?", (run,)).fetchone()[0], "cancelled")
            self.assertEqual(c.execute("SELECT status FROM crm_bots WHERE id=?", (other["id"],)).fetchone()[0], "active")
            self.assertFalse(c.execute("SELECT 1 FROM bot_events WHERE channel_id=? AND processed=0", (self.channel,)).fetchone())

    def test_media_follows_actual_message_channel_not_the_original_upload_channel(self):
        self.incoming()
        other = self.store.channel({"doctor_id": self.doc, "phone": "5500000000004", "name": "Outra conexao"})
        with self.store.db() as c:
            message = dict(c.execute("SELECT * FROM messages LIMIT 1").fetchone())
            for key, source, bound in (("bound", other, message["id"]), ("draft", self.channel, "")):
                c.execute("""INSERT INTO message_media(id,doctor_id,source_channel_id,contact_id,user_id,upload_request,name,mime,kind,size,sha256,created_at,message_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (key, self.doc, source, message["contact_id"], "operator", key, "teste.pdf", "application/pdf", "document", 1, "hash", 1, bound))
            c.execute("UPDATE messages SET media_id='bound' WHERE id=?", (message["id"],))
        payload = self.transfer_payload()
        transfer(self.store, payload, "master", "Master")
        with self.store.db() as c:
            bound = self.store.row(c, "message_media", "bound")
            self.assertEqual((bound["doctor_id"], bound["source_channel_id"]), (payload["target_doctor_id"], self.channel))
            self.assertEqual(self.store.row(c, "message_media", "draft")["doctor_id"], self.doc)
            with self.assertRaises(ValueError):
                self.store.owned(c, "message_media", "bound", self.doc)
