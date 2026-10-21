import json
import unittest
from unittest.mock import Mock

from tests import test_crm as fixtures
from crm_whatsapp_attribution import capture, tick, sync_campaigns, listing


class WhatsAppAttributionTests(unittest.TestCase):
    setUp = fixtures.CrmTests.setUp
    lead = fixtures.CrmTests.lead

    def message(self, ident="in-ad-1", source="123456"):
        return {"key": {"id": ident, "fromMe": False, "remoteJid": "5500000000099@s.whatsapp.net"},
                "pushName": "Cliente Exemplo", "message": {"extendedTextMessage": {
                    "text": "Gostaria de informacoes", "contextInfo": {"externalAdReply": {
                        "sourceType": "ad", "sourceId": source, "ctwaClid": "click-123",
                        "title": "Conheca a clinica", "sourceUrl": "https://example.test/ad?utm_source=instagram&utm_campaign=visitas&email=private",
                        "thumbnail": "private-image", "body": "private-ad-copy"}}}}}

    def receive(self, data):
        channel = self.store.listing(self.a)["channels"][0]
        return self.store.receive(channel["instance"], data, "messages.upsert")

    def history(self):
        lead = self.store.listing(self.a)["leads"][0]
        return self.store.detail(lead["id"], self.a)["attribution"]

    def metadata(self, account="800", source="123456"):
        return {"id": source, "name": "Video 1", "account_id": account,
                "campaign": {"id": "456", "name": "Campanha Exemplo"},
                "adset": {"id": "789", "name": "Conjunto Exemplo"}}

    def test_incoming_ad_creates_one_lead_and_preserves_bounded_evidence(self):
        data = self.message()
        self.assertTrue(self.receive(data))
        self.assertFalse(self.receive(data))
        history = self.history()
        self.assertEqual(history["total"], 1)
        evidence = history["first"]["data"]
        self.assertEqual(evidence["kind"], "whatsapp_ad")
        self.assertEqual(evidence["utm"]["utm_campaign"], "visitas")
        self.assertNotIn("private", json.dumps(evidence))
        self.assertEqual(evidence["meta"]["source_url"], "https://example.test/ad")
        self.assertNotIn("campaign_name", evidence["meta"])
        self.assertEqual(len(self.store.listing(self.a)["leads"]), 1)

    def test_later_campaign_keeps_first_touch_without_duplicating_contact(self):
        self.receive(self.message())
        self.receive(self.message("in-ad-2", "987654"))
        history = self.history()
        self.assertEqual(history["total"], 2)
        self.assertEqual(history["first"]["data"]["meta"]["source_id"], "123456")
        self.assertEqual(history["last"]["data"]["meta"]["source_id"], "987654")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0], 1)

    def test_plain_messages_link_previews_and_quoted_ads_are_not_paid_evidence(self):
        message = self.message()["message"]
        ad = message["extendedTextMessage"]["contextInfo"]["externalAdReply"]
        ad.pop("ctwaClid")
        ad["sourceType"] = "url"
        self.assertEqual(capture(message), {})
        quoted = {"extendedTextMessage": {"text": "Respondendo", "contextInfo": {"quotedMessage": self.message()["message"]}}}
        self.assertEqual(capture(quoted), {})
        forwarded = self.message()["message"]
        forwarded["extendedTextMessage"]["contextInfo"]["isForwarded"] = True
        self.assertEqual(capture(forwarded), {})
        self.assertEqual(capture({"conversation": "https://example.test/?utm_campaign=untrusted"}), {})

    def test_wrappers_and_invalid_tracking_do_not_drop_message(self):
        data = self.message()
        data["message"] = {"ephemeralMessage": {"message": data["message"]}}
        self.assertEqual(capture(data["message"])["meta"]["source_id"], "123456")
        data = self.message()
        ad = data["message"]["extendedTextMessage"]["contextInfo"]["externalAdReply"]
        ad["sourceUrl"] = "https://example.test/?" + "&".join(f"k{i}=v" for i in range(101))
        self.assertTrue(self.receive(data))
        self.assertEqual(self.history()["total"], 0)

    def test_outgoing_message_never_becomes_acquisition_source(self):
        data = self.message()
        data["key"]["fromMe"] = True
        self.receive(data)
        self.assertEqual(self.history()["total"], 0)

    def test_campaign_lookup_sends_only_ad_id_and_matches_configured_account(self):
        self.receive(self.message())
        request = Mock(return_value=self.metadata())
        self.assertEqual(tick(self.store, "800", request, now=10000), 1)
        request.assert_called_once_with("123456", {"fields": "id,name,account_id,campaign{id,name},adset{id,name}"})
        data = self.history()["first"]["data"]
        self.assertEqual(data["meta"]["campaign_name"], "Campanha Exemplo")
        self.assertEqual(data["utm"]["utm_campaign"], "visitas")
        self.assertNotIn("phone", json.dumps(request.call_args_list))
        tick(self.store, "800", request, now=20000)
        self.assertEqual(request.call_count, 1)

    def test_wrong_account_or_inaccessible_ad_is_not_guessed_and_retries_are_bounded(self):
        self.receive(self.message())
        request = Mock(return_value=self.metadata(account="999"))
        self.assertEqual(tick(self.store, "800", request, now=10000), 0)
        self.assertEqual(self.history()["first"]["data"]["meta"]["status"], "pending")
        tick(self.store, "800", request, now=10060)
        self.assertEqual(request.call_count, 1)
        request.side_effect = TimeoutError()
        tick(self.store, "800", request, now=14000)
        self.assertEqual(self.history()["first"]["data"]["meta"]["status"], "pending")

    def test_new_touch_reuses_cached_metadata_without_network_call(self):
        self.receive(self.message())
        request = Mock(return_value=self.metadata())
        tick(self.store, "800", request, now=10000)
        self.receive(self.message("in-ad-2"))
        self.assertEqual(tick(self.store, "800", request, now=10060), 1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.history()["last"]["data"]["meta"]["campaign_id"], "456")

    def test_ambiguous_opportunities_keep_receipt_without_guessing_campaign_assignment(self):
        self.lead(number="5500000000099", pipeline=self.store.listing(self.a)["pipelines"][0])
        self.lead(number="5500000000099", pipeline=self.store.listing(self.a)["pipelines"][1])
        with self.store.db() as c:
            # No routed pipeline: do not infer the target from whichever lead was updated last.
            from crm_whatsapp_attribution import record
            channel = dict(c.execute("SELECT * FROM channels WHERE id=?", (self.channel,)).fetchone())
        data = self.message()
        self.receive(data)
        with self.store.db() as c:
            c.execute("DELETE FROM lead_attributions")
            message = dict(c.execute("SELECT * FROM messages WHERE provider_id='in-ad-1'").fetchone())
            record(c, message, channel, None, data["message"])
            self.assertEqual(c.execute("SELECT count(*) FROM whatsapp_ad_receipts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM lead_attributions").fetchone()[0], 0)

    def test_campaign_catalog_paginates_without_following_arbitrary_next_urls(self):
        request = Mock(side_effect=[{"data": [{"id": "456", "name": "Campanha Exemplo", "effective_status": "ACTIVE"}],
                                     "paging": {"next": "https://untrusted.test/", "cursors": {"after": "page-two"}}},
                                    {"data": [{"id": "789", "name": "Outra campanha", "effective_status": "PAUSED"}]}])
        self.assertEqual(sync_campaigns(self.store, "800", request, now=10000), 2)
        self.assertEqual(request.call_args_list[1].args[0], "act_800/campaigns")
        self.assertEqual(request.call_args_list[1].args[1]["after"], "page-two")
        result = listing(self.store, self.a, "800", True)
        self.assertEqual(len(result["campaigns"]), 2)
        self.assertEqual(result["synced_at"], 10000)

    def test_facebook_listing_is_scoped_and_deduplicates_repeat_touches(self):
        self.receive(self.message())
        self.receive(self.message("in-ad-2"))
        tick(self.store, "800", Mock(return_value=self.metadata()), now=10000)
        result = listing(self.store, self.a, "800", True, "456")
        self.assertEqual(result["campaigns"][0]["identified"], 1)
        self.assertEqual(len(result["leads"]), 1)
        self.assertEqual(listing(self.store, self.b, "800", True, "456")["leads"], [])
        self.assertEqual(listing(self.store, self.a, "999", True, "456")["leads"], [])

    def test_incomplete_catalog_sync_keeps_previous_campaigns(self):
        sync_campaigns(self.store, "800", Mock(return_value={"data": [{"id": "456", "name": "Original"}]}))
        bad = Mock(side_effect=[{"data": [], "paging": {"next": "https://example.test/"}}])
        with self.assertRaises(ValueError):
            sync_campaigns(self.store, "800", bad)
        self.assertEqual(listing(self.store, self.a, "800", True)["campaigns"][0]["name"], "Original")


if __name__ == "__main__":
    unittest.main()
