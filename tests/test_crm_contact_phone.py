import unittest
from unittest.mock import Mock

from crm_access import crm_access
from crm_contact_phone import normalize, save
from crm_store import Conflict
from crm_worker import tick
from tests import test_crm as fixture


class ContactPhoneTests(unittest.TestCase):
    setUp = fixture.CrmTests.setUp
    lead = fixture.CrmTests.lead

    def change(self, key, doctor=None, **changes):
        doc = doctor or self.a
        lead = self.store.detail(key, doc)["lead"]
        return save(self.store, {"id": key, "doctor_id": doc, "phone_revision": lead["phone_revision"],
            "phone": "+55 (11) 98765-4321", "confirm": True, **changes}, {"id": "operator", "nome": "Equipe"})

    def incoming(self, number="5500000000002", provider="incoming"):
        with self.store.db() as c:
            channel = self.store.row(c, "channels", self.channel)
            c.execute("UPDATE channels SET state='open' WHERE id=?", (self.channel,))
        self.store.receive(channel["instance"], {"key": {"id": provider, "remoteJid": number + "@s.whatsapp.net"},
            "message": {"conversation": "Contato sintetico"}}, "messages.upsert")

    def test_normalization_requires_valid_country_area_and_subscriber(self):
        for value, expected in (("(11) 98765-4321", "5511987654321"), ("+55 11 98765-4321", "5511987654321"),
                                ("5511987654321", "5511987654321"), ("+1 202-555-0123", "12025550123")):
            self.assertEqual(normalize(value), expected)
        for value in ("", "+38997357320", "+55 00 98765-4321", "abc", None, "+55 11 98765-4321 ext 1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(value)

    def test_edit_preserves_contact_lead_and_history_but_changes_future_destination(self):
        key = self.lead()
        self.incoming()
        before = self.store.detail(key, self.a)
        contact = before["lead"]["contact_id"]
        self.change(key)
        after = self.store.detail(key, self.a)
        self.assertEqual(after["lead"]["id"], key)
        self.assertEqual(after["lead"]["contact_id"], contact)
        self.assertEqual(after["lead"]["phone"], "5511987654321")
        self.assertEqual(after["lead"]["phone_revision"], 2)
        self.assertEqual(after["messages"], before["messages"])
        self.assertEqual(self.store.listing(self.a)["leads"][0]["phone"], "5511987654321")
        thread = self.store.conversation(self.a, self.channel, contact)
        self.assertEqual(thread["reply_phone"], "5511987654321")
        payload = {"doctor_id": self.a, "channel_id": self.channel, "contact_id": contact, "body": "Nova resposta", "request_id": "new-phone"}
        with self.assertRaises(Conflict):
            self.store.reply({**payload, "phone": "5500000000002"}, "operator", True, sender_name="Equipe")
        self.store.reply({**payload, "phone": thread["reply_phone"]}, "operator", True, sender_name="Equipe")
        gateway = Mock()
        gateway.send.return_value = "accepted-new-phone"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["phone"], "5511987654321")
        with self.store.db() as c:
            audit = c.execute("SELECT * FROM contact_phone_edits").fetchone()
            self.assertEqual((audit["old_phone"], audit["new_phone"]), ("5500000000002", "5511987654321"))

    def test_remove_is_soft_and_blocks_sends_without_deleting_conversations(self):
        key = self.lead()
        self.incoming()
        contact = self.store.detail(key, self.a)["lead"]["contact_id"]
        self.change(key, remove=True)
        lead = self.store.detail(key, self.a)["lead"]
        self.assertEqual(lead["phone"], "")
        self.assertTrue(lead["phone_removed"])
        self.assertEqual(self.store.inbox(self.a, "operator")["conversations"][0]["phone"], "")
        thread = self.store.conversation(self.a, self.channel, contact)
        self.assertEqual(len(thread["messages"]), 1)
        self.assertEqual(thread["reply_phone"], "")
        self.assertFalse(thread["reply_allowed"])
        with self.assertRaisesRegex(ValueError, "Telefone removido"):
            self.store.reply({"doctor_id": self.a, "channel_id": self.channel, "contact_id": contact,
                "body": "Nao enviar", "request_id": "blocked"}, "operator", True, sender_name="Equipe")
        self.incoming(provider="after-removal")
        self.assertEqual(self.store.detail(key, self.a)["lead"]["phone"], "")
        self.assertEqual(len(self.store.conversation(self.a, self.channel, contact)["messages"]), 2)
        self.store.lead({**lead, "title": "Ainda pode mudar etapa"}, "Equipe")
        self.assertEqual(self.store.detail(key, self.a)["lead"]["phone"], "")
        self.change(key)
        self.assertTrue(self.store.conversation(self.a, self.channel, contact)["reply_allowed"])
        self.assertEqual(self.store.detail(key, self.a)["lead"]["phone_revision"], 3)

    def test_stale_revision_no_confirmation_and_collision_do_not_change_anything(self):
        key = self.lead()
        self.change(key)
        for changes in ({"phone_revision": 1}, {"confirm": False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.change(key, **changes)
        self.lead("5511912345678")
        with self.assertRaises(Conflict):
            self.change(key, phone="+55 11 91234-5678")
        self.assertEqual(self.store.detail(key, self.a)["lead"]["phone"], "5511987654321")

    def test_shared_contact_cannot_be_changed_outside_staff_scope(self):
        key = self.lead()
        target = self.store.listing(self.b)["pipelines"][0]
        other = self.lead(doctor=self.b, pipeline=target)
        with crm_access({self.a: None}, self.a):
            self.assertFalse(self.store.detail(key, self.a)["lead"]["can_edit_phone"])
            with self.assertRaises(PermissionError):
                self.change(key)
        with crm_access({self.a: None, self.b: None}, self.a):
            self.change(key)
        self.assertEqual(self.store.detail(other, self.b)["lead"]["phone"], "5511987654321")

    def test_unknown_is_preserved_old_queue_is_stopped_and_inflight_blocks_edit(self):
        key = self.lead()
        self.incoming()
        contact = self.store.detail(key, self.a)["lead"]["contact_id"]
        payload = {"doctor_id": self.a, "channel_id": self.channel, "contact_id": contact, "body": "Antigo", "request_id": "old"}
        unknown = self.store.reply(payload, "operator", True, sender_name="Equipe")
        claimed = self.store.claim()
        with self.assertRaises(Conflict):
            self.change(key)
        self.store.finish(claimed, error=True)
        queued = self.store.reply({**payload, "body": "Fila antiga", "request_id": "queued"}, "operator", True, sender_name="Equipe")
        self.change(key)
        with self.store.db() as c:
            self.assertEqual(self.store.row(c, "messages", unknown)["status"], "unknown")
            self.assertEqual(self.store.row(c, "messages", queued)["status"], "skipped")
        gateway = Mock()
        self.assertFalse(tick(self.store, gateway))
        gateway.send.assert_not_called()

    def test_different_experts_patient_is_rejected_without_rebinding(self):
        self.store.patient_lookup = lambda number: {"status": "matched", "uuid": "original", "name": "Contato A"}
        key = self.lead()
        self.store.patient_lookup = lambda number: {"status": "matched", "uuid": "other" if number == "5511987654321" else "original", "name": "Contato A"}
        with self.assertRaises(Conflict):
            self.change(key)
        self.assertEqual(self.store.detail(key, self.a)["lead"]["experts_uuid"], "original")


if __name__ == "__main__":
    unittest.main()
