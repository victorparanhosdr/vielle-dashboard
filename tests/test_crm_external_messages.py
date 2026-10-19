import io
import json
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from crm_api import handle_request
from crm_gateway import EvolutionGateway
from crm_message_sync import sync_conversation
from crm_store import whatsapp_content
from tests import test_crm as base


class ExternalMessageTests(unittest.TestCase):
    setUp = base.CrmTests.setUp
    lead = base.CrmTests.lead
    campaign = base.CrmTests.campaign
    enable = base.CrmTests.enable

    def message(self, identifier, when, body="Resposta pelo celular", *, outgoing=True, channel=None, history=False, **changes):
        with self.store.db() as c:
            sender = self.store.row(c, "channels", channel or self.channel)
        payload = {"key": {"id": identifier, "remoteJid": "5533999990001@s.whatsapp.net", "fromMe": outgoing},
            "pushName": "Atendente do WhatsApp" if outgoing else "Paciente", "message": {"conversation": body},
            "messageTimestamp": when, **changes}
        return self.store.receive(sender["instance"], payload, "send.message" if outgoing else "messages.upsert", history=history)

    def contact(self):
        key = self.lead("5533999990001")
        return self.store.detail(key, self.a)["lead"]

    def thread(self, contact, channel=None):
        return self.store.conversation(self.a, channel or self.channel, contact["contact_id"])

    def test_external_reply_is_visible_idempotent_and_only_resolves_previous_inbound(self):
        contact = self.contact()
        clock = int(time.time()) - 120
        self.message("in", clock, "Quero agendar", outgoing=False)
        self.assertEqual(self.thread(contact)["unread"], 1)
        self.assertTrue(self.message("out", clock + 10))
        self.assertFalse(self.message("out", clock + 10))
        thread = self.thread(contact)
        self.assertEqual(thread["unread"], 0)
        self.assertEqual(thread["resolution"], "replied")
        self.assertEqual(thread["messages"][-1]["origin"], "external")
        self.assertEqual(thread["messages"][-1]["sender_name"], "WhatsApp / sistema externo")
        self.assertEqual(thread["contact"]["name"], "Cliente Exemplo")
        self.message("new-in", clock + 20, "Pode ser amanha?", outgoing=False)
        self.assertEqual(self.thread(contact)["unread"], 1)
        self.assertEqual(self.store.inbox(self.a, "user")["counts"]["unread"], 1)

    def test_same_second_new_message_is_not_marked_answered_by_an_earlier_reply(self):
        contact = self.contact()
        clock = int(time.time()) - 120
        self.message("in", clock, outgoing=False)
        self.message("out", clock)
        self.assertEqual(self.thread(contact)["unread"], 0)
        self.message("new-in", clock, outgoing=False)
        self.assertEqual(self.thread(contact)["unread"], 1)

    def test_history_is_chronological_and_old_answers_do_not_clear_newer_questions(self):
        contact = self.contact()
        clock = int(time.time()) - 120
        self.message("question", clock + 50, "Pergunta mais recente", outgoing=False)
        self.message("old-answer", clock, history=True)
        self.message("new-answer", clock + 40, history=True)
        self.assertEqual(self.thread(contact)["unread"], 1)
        self.assertEqual([m["provider_id"] for m in self.thread(contact)["messages"]], ["old-answer", "new-answer", "question"])
        self.assertEqual(self.store.inbox(self.a, "user")["conversations"][0]["body"], "Pergunta mais recente")
        self.message("actual-answer", clock + 60, history=True)
        self.assertEqual(self.thread(contact)["unread"], 0)
        self.assertEqual(self.store.inbox(self.a, "user")["counts"]["unread"], 0)

    def test_external_history_paginates_without_losing_same_timestamp_records(self):
        contact = self.contact()
        clock = int(time.time()) - 120
        for i in range(85):
            self.message(f"old-{i:03}", clock, history=True)
        newest = self.thread(contact)
        self.assertEqual(len(newest["messages"]), 80)
        self.assertTrue(newest["has_older"])
        older = self.store.conversation(self.a, self.channel, contact["contact_id"], newest["messages"][0]["seq"])
        self.assertEqual(len(older["messages"]), 5)
        self.assertFalse(older["has_older"])
        self.assertEqual(len({m["id"] for m in newest["messages"] + older["messages"]}), 85)
        with self.assertRaises(ValueError):
            self.store.conversation(self.a, self.channel, contact["contact_id"], 999999)

    def test_missing_timestamp_is_not_evidence_of_a_reply_and_history_skips_it(self):
        contact = self.contact()
        clock = int(time.time()) - 120
        self.message("in", clock, outgoing=False)
        self.message("undated-out", None)
        self.assertEqual(self.thread(contact)["unread"], 1)
        self.assertFalse(self.message("undated-history", None, history=True))

    def test_external_response_does_not_close_other_sending_number_or_reactivate_optout(self):
        contact = self.contact()
        second = self.store.channel({"doctor_id": self.a, "name": "Recepcao", "phone": "5511999990002"})
        clock = int(time.time()) - 120
        self.message("in-one", clock, outgoing=False)
        self.message("in-two", clock, outgoing=False, channel=second)
        self.message("out", clock + 10, body="SAIR")
        self.assertEqual(self.thread(contact)["unread"], 0)
        self.assertEqual(self.thread(contact, second)["unread"], 1)
        self.assertFalse(self.thread(contact)["contact"]["opted_out"])
        self.message("optout", clock + 20, body="SAIR", outgoing=False)
        self.message("later-out", clock + 30)
        self.assertTrue(self.thread(contact)["contact"]["opted_out"])

    def test_crm_send_echo_before_http_response_does_not_duplicate_or_close_campaign_thread(self):
        key = self.lead("5533999990001")
        contact = self.store.detail(key, self.a)["lead"]
        self.message("in", int(time.time()) - 120, outgoing=False)
        campaign = self.campaign([key])
        self.enable(campaign)
        item = self.store.claim(self.now)
        with patch("crm_store.time.time", return_value=self.now + 1):
            self.assertFalse(self.message("echo", self.now + 1, body=item["body"]))
        self.store.finish(item, "echo")
        self.assertFalse(self.message("echo", self.now + 1, body=item["body"]))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE direction='out'").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT origin FROM messages WHERE direction='out'").fetchone()[0], "crm")
        self.assertEqual(self.thread(contact)["unread"], 1)

    def test_media_wrappers_groups_and_lid_are_handled_without_guessing_a_phone(self):
        contact = self.contact()
        clock = int(time.time()) - 120
        self.assertEqual(whatsapp_content({"ephemeralMessage": {"message": {"extendedTextMessage": {"text": "Texto"}}}}), "Texto")
        self.assertEqual(whatsapp_content({"audioMessage": {}}), "[Audio]")
        self.assertEqual(whatsapp_content({"imageMessage": {"caption": "Foto"}}), "[Imagem] Foto")
        self.assertEqual(whatsapp_content({"extendedTextMessage": "invalid"}), "")
        self.assertEqual(whatsapp_content({"protocolMessage": {}}), "")
        self.assertFalse(self.message("group", clock, key={"id": "group", "fromMe": True, "remoteJid": "123@g.us"}))
        self.assertFalse(self.message("lid", clock, key={"id": "lid", "fromMe": True, "remoteJid": "123@lid"}))
        self.assertTrue(self.message("lid-alt", clock, key={"id": "lid-alt", "fromMe": True, "remoteJid": "123@lid", "remoteJidAlt": "5533999990001@s.whatsapp.net"}))
        self.assertEqual(len(self.thread(contact)["messages"]), 1)

    def test_sync_recovers_only_outgoing_for_owned_contact_and_has_a_cooldown(self):
        contact = self.contact()
        clock = int(time.time()) - 120
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open'")
        gateway = Mock()
        gateway.history.return_value = ([
            {"key": {"id": "old-out", "fromMe": True, "remoteJid": "5533999990001@s.whatsapp.net"}, "message": {"conversation": "Resposta antiga"}, "messageTimestamp": clock},
            {"key": {"id": "wrong", "fromMe": True, "remoteJid": "5511999990001@s.whatsapp.net"}, "message": {"conversation": "Outro contato"}, "messageTimestamp": clock},
            {"key": {"id": "old-in", "fromMe": False, "remoteJid": "5533999990001@s.whatsapp.net"}, "message": {"conversation": "Entrada antiga"}, "messageTimestamp": clock}], True)
        payload = {"doctor_id": self.a, "channel_id": self.channel, "contact_id": contact["contact_id"]}
        result = sync_conversation(self.store, gateway, payload)
        self.assertEqual(result["imported"], 1)
        self.assertTrue(result["limited"])
        self.assertTrue(sync_conversation(self.store, gateway, payload)["cached"])
        gateway.history.assert_called_once()
        self.assertEqual(len(self.thread(contact)["messages"]), 1)
        with self.assertRaises(ValueError):
            sync_conversation(self.store, gateway, {**payload, "doctor_id": self.b})

    def test_gateway_webhook_updates_without_repairing_and_history_is_scoped(self):
        gateway = EvolutionGateway()
        gateway.request = Mock(return_value={"messages": {"records": [], "pages": 2}})
        channel = {"instance": "example"}
        with patch.dict("os.environ", {"CRM_WEBHOOK_URL": "https://example.test/api/crm-webhook", "CRM_WEBHOOK_SECRET": "x" * 40}):
            gateway.configure_webhook(channel)
        self.assertEqual(gateway.request.call_args.args[1], "/webhook/set/example")
        self.assertIn("SEND_MESSAGE", gateway.request.call_args.args[2]["webhook"]["events"])
        self.assertEqual(gateway.history(channel, "5533999990001"), ([], True))
        self.assertEqual(gateway.request.call_args.args[2]["where"], {"key": {"remoteJid": "5533999990001@s.whatsapp.net"}})
        self.assertEqual(gateway.request.call_args.args[2]["offset"], 200)
        gateway.request.return_value = {"messages": {"records": "invalid"}}
        with self.assertRaises(ValueError):
            gateway.history(channel, "5533999990001")

    def test_sync_http_keeps_master_clinic_origin_and_ownership_guards(self):
        contact = self.contact()
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open'")
        payload = {"doctor_id": self.a, "channel_id": self.channel, "contact_id": contact["contact_id"]}
        parsed = urlparse("https://example.test/api/crm/conversation-sync?clinic=vielle")
        def handler(master=True, origin=True, body=payload):
            h = Mock()
            h.current_user = {"id": "1", "nome": "Equipe", "is_master": master}
            h.command = "POST"
            raw = json.dumps(body).encode()
            h.headers = {"Content-Length": str(len(raw))}
            h.rfile = io.BytesIO(raw)
            h.same_origin_request.return_value = origin
            return h
        with patch("crm_api.store_for", return_value=self.store), patch("crm_api.EvolutionGateway") as gateway:
            gateway.return_value.history.return_value = ([], False)
            for h, code in ((handler(master=False), 403), (handler(origin=False), 403),
                            (handler(body={**payload, "doctor_id": self.b}), 400)):
                handle_request(h, parsed)
                self.assertEqual(h.auth_json.call_args.args[1], code)
            gateway.return_value.history.assert_not_called()
            h = handler()
            handle_request(h, parsed)
            self.assertTrue(h.auth_json.call_args.args[0]["ok"])
            gateway.return_value.history.assert_called_once()
