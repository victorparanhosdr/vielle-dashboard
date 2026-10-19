from io import BytesIO
import tempfile
import unittest
from unittest.mock import Mock, patch

from crm_avatars import download, filename, refresh, serve
from crm_gateway import EvolutionGateway
from crm_store import CrmStore


class AvatarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(self.tmp.name)
        self.store.bootstrap({"Doutor A": ["Funil"], "Doutor B": ["Funil"]})
        self.a, self.b = [d["id"] for d in self.store.listing()["doctors"]]
        pipe = self.store.listing(self.a)["pipelines"][0]
        self.lid = self.store.lead({"doctor_id": self.a, "pipeline_id": pipe["id"], "stage_id": pipe["stages"][0]["id"],
                                    "name": "Nome do paciente", "phone": "5533999991111", "title": "Plano"}, "Equipe")
        self.contact = self.store.detail(self.lid, self.a)["lead"]["contact_id"]
        self.channel = self.store.channel({"doctor_id": self.a, "name": "Atendimento", "phone": "5511999992222"})
        self.other = self.store.channel({"doctor_id": self.b, "name": "Outro", "phone": "5511999993333"})
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open'")
        self.gateway = Mock(configured=True)
        self.gateway.profile_picture.return_value = "https://pps.whatsapp.net/photo?private=signed"
        self.fetch = Mock(return_value=b"jpeg fixture")

    def test_photo_is_cached_private_and_not_identity_or_message(self):
        data = refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)
        self.assertEqual(data["state"], "ready")
        self.assertTrue(data["url"].startswith("/api/crm/avatar?"))
        self.assertNotIn("signed", data["url"])
        again = refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)
        self.assertEqual(data, again)
        self.gateway.profile_picture.assert_called_once()
        handler = Mock(wfile=BytesIO())
        serve(handler, self.store, self.a, self.contact, self.channel)
        handler.send_header.assert_any_call("Cache-Control", "private, no-store")
        handler.send_header.assert_any_call("X-Content-Type-Options", "nosniff")
        self.assertEqual(handler.wfile.getvalue(), b"jpeg fixture")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT name,experts_uuid,opted_out FROM contacts").fetchone()[:], ("Nome do paciente", "", 0))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_doctor_channel_and_contact_boundaries(self):
        for doctor, channel in ((self.b, ""), (self.a, self.other)):
            with self.assertRaises((LookupError, ValueError)):
                refresh(self.store, self.gateway, doctor, self.contact, channel, fetch=self.fetch)
        refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)
        with self.assertRaises((LookupError, ValueError)):
            serve(Mock(), self.store, self.b, self.contact, self.channel)

    def test_missing_photo_and_unavailable_connector_keep_initials(self):
        self.gateway.profile_picture.return_value = None
        self.assertEqual(refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)["url"], "")
        self.fetch.assert_not_called()
        self.gateway.profile_picture.assert_called_once()
        self.assertEqual(refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)["state"], "missing")
        self.gateway.profile_picture.assert_called_once()
        self.gateway.configured = False
        self.assertEqual(refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)["state"], "unavailable")

    def test_failure_is_throttled_and_does_not_leak_provider_errors(self):
        self.gateway.profile_picture.side_effect = RuntimeError("API_KEY private URL")
        data = refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)
        self.assertEqual(data, {"state": "unavailable", "url": "", "retry_after": 0})
        refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)
        self.gateway.profile_picture.assert_called_once()

    def test_cache_is_invalidated_for_changed_recipient_and_removed_photo(self):
        refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)
        with self.store.db() as c:
            c.execute("UPDATE contacts SET phone='5533999994444'")
        with self.assertRaises(LookupError): serve(Mock(), self.store, self.a, self.contact, self.channel)
        refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)
        self.assertEqual(self.gateway.profile_picture.call_args.args[1], "5533999994444")
        with self.store.db() as c: c.execute("UPDATE contact_avatars SET checked_at=0")
        self.gateway.profile_picture.return_value = None
        self.assertEqual(refresh(self.store, self.gateway, self.a, self.contact, fetch=self.fetch)["url"], "")
        self.assertFalse(filename(self.store, self.channel, self.contact, "5533999994444").exists())

    def test_two_sessions_do_not_cross_share_avatar(self):
        other_channel = self.store.channel({"doctor_id": self.a, "name": "Segunda linha", "phone": "5511999995555"})
        with self.store.db() as c: c.execute("UPDATE channels SET state='open'")
        refresh(self.store, self.gateway, self.a, self.contact, self.channel, fetch=self.fetch)
        with self.assertRaises(LookupError): serve(Mock(), self.store, self.a, self.contact, other_channel)
        refresh(self.store, self.gateway, self.a, self.contact, other_channel, fetch=self.fetch)
        self.assertEqual(self.gateway.profile_picture.call_count, 2)

    def test_download_rejects_non_whatsapp_urls_and_svg(self):
        for url in ("http://pps.whatsapp.net/a", "https://127.0.0.1/a", "https://pps.whatsapp.net.evil.test/a",
                    "https://user:secret@pps.whatsapp.net/a", "https://pps.whatsapp.net:8080/a", "file:///etc/passwd"):
            with self.assertRaises(ValueError): download(url)
        response = Mock()
        response.headers.get_content_type.return_value = "image/svg+xml"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("crm_avatars.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(ValueError): download("https://pps.whatsapp.net/a")

    def test_download_reencodes_and_limits_real_bitmap(self):
        from PIL import Image
        raw = BytesIO()
        Image.new("RGB", (300, 400), "green").save(raw, format="PNG")
        response = Mock()
        response.headers.get_content_type.return_value = "image/png"
        response.read.return_value = raw.getvalue()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("crm_avatars.build_opener") as opener:
            opener.return_value.open.return_value = response
            encoded = download("https://pps.whatsapp.net/a?secret=signed")
            request = opener.return_value.open.call_args.args[0]
            self.assertNotIn("Apikey", request.headers)
            with Image.open(BytesIO(encoded)) as image:
                self.assertEqual((image.format, image.size), ("JPEG", (160, 160)))
            response.read.return_value = b"x" * (512 * 1024 + 1)
            with self.assertRaises(ValueError): download("https://pps.whatsapp.net/a")

    def test_gateway_uses_only_profile_read_endpoint(self):
        gateway = EvolutionGateway()
        with patch.object(gateway, "request", return_value={"profilePictureUrl": None}) as request:
            self.assertEqual(gateway.profile_picture({"instance": "safe instance"}, "5533999991111"), "")
            request.assert_called_once_with("POST", "/chat/fetchProfilePictureUrl/safe%20instance", {"number": "5533999991111"})

    def test_inbox_summary_keeps_doctor_scoped_values_location_and_unread(self):
        with self.store.db() as c:
            channel = self.store.row(c, "channels", self.channel)
        self.store.receive(channel["instance"], {"key": {"id": "incoming-1", "remoteJid": "5533999991111@s.whatsapp.net"},
                                               "message": {"conversation": "Quero saber do orçamento"}}, "messages.upsert")
        with self.store.db() as c:
            c.execute("UPDATE contacts SET experts_uuid='p1',experts_status='matched',service_location='capelinha'")
        self.store.commercial_source = Mock()
        self.store.commercial_source.summaries.return_value = {"p1": {"state": "ready", "summary": {
            "sales_cents": 1800000, "sales_count": 2, "pending_quotes_cents": 350000,
            "pending_quotes_count": 2, "open_quotes_count": 1, "expired_quotes_count": 1}}}
        item = self.store.inbox(self.a, "user")["conversations"][0]
        self.assertEqual(item["service_location"], "capelinha")
        self.assertEqual(item["customer_value"]["tier"], "gold")
        self.assertEqual(item["customer_history"]["pending_quotes_cents"], 350000)
        self.assertEqual(item["unread"], 1)
        self.store.commercial_source.summaries.assert_called_once_with(["p1"], "Doutor A", include_search=False)
        self.assertEqual(self.store.inbox(self.b, "user")["conversations"], [])


if __name__ == "__main__":
    unittest.main()
