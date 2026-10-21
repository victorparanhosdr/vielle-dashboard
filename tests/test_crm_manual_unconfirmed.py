from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from unittest.mock import Mock

from crm_store import CrmStore, Conflict
from crm_worker import tick
from tests import test_crm as base
from tests import test_crm_media as media_fixture


class ManualUnconfirmedTests(unittest.TestCase):
    setUp = media_fixture.MediaTests.setUp
    lead = base.CrmTests.lead
    upload = media_fixture.MediaTests.upload
    reply = media_fixture.MediaTests.reply
    campaign = base.CrmTests.campaign

    def text(self, body, request, contact=None, channel=None):
        contact, channel = contact or self.contact, channel or self.channel
        return self.store.reply({"doctor_id": self.a, "channel_id": channel, "contact_id": contact,
            "body": body, "request_id": request}, "operator", True, sender_name="Jane Equipe")

    def unknown(self, body="Mensagem anterior", contact=None):
        message = self.text(body, "unconfirmed-first", contact)
        gateway = Mock()
        gateway.send.side_effect = TimeoutError()
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(self.message(message)["status"], "unknown")
        return message

    def message(self, key):
        with self.store.db() as c:
            return self.store.row(c, "messages", key)

    def another_contact(self):
        lead = self.lead("5500000000099")
        return self.store.detail(lead, self.a)["lead"]["contact_id"]

    def test_other_contact_uncertainty_does_not_block_pdf_or_duplicate_dispatch(self):
        previous = self.unknown(contact=self.another_contact())
        attachment = self.upload("document", "orcamento.pdf", media_fixture.PDF)
        target = self.reply(attachment)
        gateway = Mock()
        gateway.send.return_value = "accepted-pdf"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["id"], target)
        self.assertEqual(gateway.send.call_args.args[0]["media"]["kind"], "document")
        self.assertEqual(self.message(target)["status"], "sent")
        self.assertEqual(self.message(previous)["status"], "unknown")
        self.assertEqual(self.reply(attachment), target)
        self.assertFalse(tick(self.store, gateway))
        gateway.send.assert_called_once()

    def test_distinct_messages_in_the_same_conversation_continue(self):
        previous = self.unknown()
        attachment = self.upload("document", "novo-orcamento.pdf", media_fixture.PDF)
        target = self.reply(attachment)
        gateway = Mock()
        gateway.send.return_value = "new-content"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["id"], target)
        self.assertEqual(self.message(previous)["status"], "unknown")
        with self.assertRaisesRegex(ValueError, "Esta mensagem"):
            self.text("Mensagem anterior", "do-not-duplicate")
        self.assertEqual(self.text("Mensagem anterior", "unconfirmed-first"), previous)
        self.assertFalse(tick(self.store, gateway))

    def test_attachment_hash_rejects_same_file_with_new_upload_id_or_filename(self):
        first = self.upload("document", "orcamento.pdf", media_fixture.PDF)
        previous = self.reply(first)
        gateway = Mock()
        gateway.send.side_effect = TimeoutError()
        tick(self.store, gateway)
        again = self.upload("document", "renomeado.pdf", media_fixture.PDF, request="upload-2")
        with self.assertRaisesRegex(ValueError, "Esta mensagem"):
            self.reply(again, request="reply-2")
        self.assertEqual(self.message(previous)["status"], "unknown")
        distinct = self.upload("document", "orcamento-corrigido.pdf", media_fixture.PDF + b"% updated\n", request="upload-3")
        target = self.reply(distinct, request="reply-3")
        gateway.send.side_effect = None
        gateway.send.return_value = "accepted-correction"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["id"], target)

    def test_uncertain_audio_signature_keeps_same_audio_blocked_not_other_files(self):
        audio = self.upload("audio", "audio.wav", media_fixture.WAV)
        child = self.reply(audio)
        gateway = Mock()
        gateway.send.side_effect = TimeoutError()
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(self.message(child)["status"], "queued")
        self.assertEqual(self.message(self.message(child)["depends_on"])["status"], "unknown")
        again = self.upload("audio", "audio-novo-nome.wav", media_fixture.WAV, request="upload-2")
        with self.assertRaisesRegex(ValueError, "Esta mensagem"):
            self.reply(again, request="reply-2")
        attachment = self.upload("document", "orcamento.pdf", media_fixture.PDF, request="upload-pdf")
        target = self.reply(attachment, request="reply-pdf")
        gateway.send.side_effect = None
        gateway.send.return_value = "accepted-document"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["id"], target)
        self.assertEqual(self.message(child)["status"], "queued")
        self.assertFalse(tick(self.store, gateway))

    def test_distinct_audio_retains_signature_dependency_and_is_sent_once(self):
        self.unknown()
        audio = self.upload("audio", "audio.wav", media_fixture.WAV)
        child = self.reply(audio)
        gateway = Mock()
        gateway.send.return_value = "signature"
        self.assertTrue(tick(self.store, gateway))
        parent = gateway.send.call_args.args[0]
        self.assertEqual(parent["id"], self.message(child)["depends_on"])
        gateway.send.return_value = "audio"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["id"], child)
        self.assertFalse(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_count, 2)

    def test_new_queued_duplicate_is_skipped_when_first_send_becomes_unknown(self):
        previous = self.text("Mensagem repetida", "first")
        duplicate = self.text("Mensagem repetida", "second")
        next_message = self.text("Outra resposta", "third")
        gateway = Mock()
        gateway.send.side_effect = TimeoutError()
        tick(self.store, gateway)
        gateway.send.side_effect = None
        gateway.send.return_value = "third-accepted"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["id"], next_message)
        self.assertEqual(self.message(previous)["status"], "unknown")
        self.assertEqual(self.message(duplicate)["status"], "skipped")
        self.assertIn("sem confirmacao", self.message(duplicate)["skip_reason"])
        self.assertFalse(tick(self.store, gateway))

    def test_upgrade_does_not_release_old_queued_messages_or_clear_unknown(self):
        previous = self.text("Anterior", "first")
        legacy = self.text("Fila antiga", "legacy")
        with self.store.db() as c:
            c.execute("UPDATE messages SET independent_manual_send=0")
        gateway = Mock()
        gateway.send.side_effect = TimeoutError()
        tick(self.store, gateway)
        self.store = CrmStore(self.tmp.name)
        self.assertFalse(tick(self.store, gateway))
        self.assertEqual(self.message(legacy)["status"], "queued")
        self.assertEqual(self.message(legacy)["independent_manual_send"], 0)
        target = self.text("Novo envio solicitado", "new")
        gateway.send.side_effect = None
        gateway.send.return_value = "new-accepted"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["id"], target)
        self.assertEqual(self.message(previous)["status"], "unknown")
        self.assertEqual(self.message(legacy)["status"], "queued")

    def test_queued_audio_duplicate_is_skipped_before_another_signature_is_sent(self):
        first = self.upload("audio", "audio.wav", media_fixture.WAV)
        original = self.reply(first)
        second = self.upload("audio", "outro-nome.wav", media_fixture.WAV, request="second-upload")
        duplicate = self.reply(second, request="second-reply")
        gateway = Mock()
        gateway.send.side_effect = TimeoutError()
        self.assertTrue(tick(self.store, gateway))
        self.assertFalse(tick(self.store, gateway))
        self.assertEqual(self.message(self.message(original)["depends_on"])["status"], "unknown")
        self.assertEqual(self.message(original)["status"], "queued")
        self.assertEqual(self.message(duplicate)["status"], "skipped")
        self.assertEqual(self.message(self.message(duplicate)["depends_on"])["status"], "skipped")
        gateway.send.assert_called_once()

    def test_one_in_flight_per_number_and_stale_send_is_never_requeued(self):
        previous = self.unknown()
        first = self.text("Nova resposta 1", "new-1")
        second = self.text("Nova resposta 2", "new-2")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.claim(), range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        claimed = next(r for r in results if r)
        self.assertEqual(claimed["id"], first)
        stale = self.store.claim(time.time() + 200)
        self.assertEqual(stale["id"], second)
        self.assertEqual(self.message(first)["status"], "unknown")
        self.assertEqual(self.message(previous)["status"], "unknown")
        self.store.finish(stale, "accepted-second")
        self.assertIsNone(self.store.claim())

    def test_campaign_does_not_resume_behind_the_manual_reply(self):
        self.unknown()
        campaign = self.campaign(ids=[self.key])
        with self.assertRaisesRegex(Conflict, "Outro envio"):
            self.store.campaign_status(campaign, self.a, "start", True)
        target = self.text("Atendimento manual", "new")
        gateway = Mock()
        gateway.send.return_value = "accepted-manual"
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual(gateway.send.call_args.args[0]["id"], target)
        self.assertFalse(tick(self.store, gateway))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT status FROM messages WHERE campaign_id=?", (campaign,)).fetchone()[0], "queued")

    def test_duplicate_guard_is_scoped_to_recipient_and_sender_number(self):
        previous = self.unknown()
        other = self.another_contact()
        second = self.store.channel({"doctor_id": self.a, "name": "Recepcao", "phone": "5500000000004"})
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open' WHERE id=?", (second,))
        messages = [self.text("Mensagem anterior", "other-person", other),
                    self.text("Mensagem anterior", "other-number", channel=second)]
        gateway = Mock()
        gateway.send.side_effect = ["other-person-accepted", "other-number-accepted"]
        self.assertTrue(tick(self.store, gateway))
        self.assertTrue(tick(self.store, gateway))
        self.assertEqual({call.args[0]["id"] for call in gateway.send.call_args_list}, set(messages))
        self.assertEqual(self.message(previous)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
