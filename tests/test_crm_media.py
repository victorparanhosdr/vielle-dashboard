import base64
import io
import json
import os
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlparse

from crm_api import handle_request
from crm_gateway import EvolutionGateway
from crm_media import LIMITS, attach, extract, fetch_media, for_send, path_for, serve, upload, validate
from crm_store import Conflict, CrmStore
from crm_worker import tick
from tests import test_crm as base

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3qkAAAAASUVORK5CYII=")
WAV = b"RIFF" + b"\0" * 4 + b"WAVEfmt " + b"\0" * 40
PDF = b"%PDF-1.4\n%%EOF\n"
VIDEO = b"\0\0\0\x18ftypisom" + b"\0" * 32


class MediaTests(unittest.TestCase):
    lead = base.CrmTests.lead

    def setUp(self):
        base.CrmTests.setUp(self)
        self.key = self.lead()
        self.contact = self.store.detail(self.key, self.a)["lead"]["contact_id"]
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open'")
            self.instance = self.store.row(c, "channels", self.channel)["instance"]
        self.store.receive(self.instance, {"key": {"id": "inbound", "remoteJid": "5500000000002@s.whatsapp.net"},
            "message": {"conversation": "Pode mandar"}}, "messages.upsert")

    def upload(self, kind="image", name="teste.png", data=PNG, request="upload-1", user="operator"):
        return upload(self.store, self.a, self.channel, self.contact, user, request, name, kind, data)

    def reply(self, media, request="reply-1", **changes):
        cursor = self.store.conversation(self.a, self.channel, self.contact)["inbound_cursor"]
        return self.store.reply({"doctor_id": self.a, "channel_id": self.channel, "source_channel_id": self.channel,
            "contact_id": self.contact, "body": "", "media_id": media["id"], "request_id": request, "cursor": cursor, **changes},
            "operator", True, sender_name="Jane Equipe")

    def test_upload_is_private_idempotent_and_never_sends(self):
        media = self.upload()
        self.assertEqual(media, self.upload())
        path = path_for(self.store, media["id"])
        self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(media["url"].startswith("/api/crm/media?clinic=vielle&doctor="))
        self.assertNotIn("path", media)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE direction='out'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM message_media").fetchone()[0], 1)
        with self.assertRaises(Conflict):
            self.upload(name="outro.png")
        with self.assertRaises(ValueError):
            upload(self.store, self.b, self.channel, self.contact, "operator", "foreign", "teste.png", "image", PNG)

    def test_formats_magic_size_and_paths_are_validated(self):
        for kind, name, data in (("image", "a.png", PNG), ("audio", "a.wav", WAV), ("video", "a.mp4", VIDEO),
                                 ("document", "a.pdf", PDF), ("document", "a.txt", b"Ola")):
            self.assertTrue(validate(name, kind, data)[1])
        for name, kind, data in (("../a.png", "image", PNG), ("a\\b.png", "image", PNG), ("a\r\n.png", "image", PNG),
                                 ("a.svg", "image", b"<svg/>"), ("a.png", "image", b"<script/>"), ("a.exe", "document", b"MZ"),
                                 ("a.pdf", "image", PDF), ("a.txt", "document", b"\0\0"), ("a.wav", "audio", b"")):
            with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                validate(name, kind, data)
        with patch.dict(LIMITS, image=4), self.assertRaises(ValueError):
            validate("a.png", "image", PNG)
        for key in ("../private", "/tmp/file", "not-a-uuid"):
            with self.assertRaises(ValueError):
                path_for(self.store, key)

    def test_image_reply_is_queued_once_scoped_and_keeps_unread_until_sent(self):
        media = self.upload()
        message = self.reply(media)
        self.assertEqual(message, self.reply(media))
        with self.assertRaises(Conflict):
            self.reply(media, request="reuse")
        self.assertEqual(self.store.conversation(self.a, self.channel, self.contact)["unread"], 1)
        gateway = Mock()
        gateway.send.return_value = "sent-image"
        self.assertTrue(tick(self.store, gateway))
        item = gateway.send.call_args.args[0]
        self.assertEqual(base64.b64decode(item["media"]["base64"]), PNG)
        self.assertEqual(item["media"]["kind"], "image")
        self.assertEqual(item["body"], "*Jane Equipe*")
        conversation = self.store.conversation(self.a, self.channel, self.contact)
        self.assertEqual(conversation["unread"], 0)
        self.assertEqual(conversation["messages"][-1]["media"], media)
        self.assertEqual(conversation["messages"][-1]["status"], "sent")
        self.assertFalse(tick(self.store, gateway))

    def test_blank_attachment_captions_never_send_generated_labels_or_filenames(self):
        for kind, name, data in (("image", "foto.png", PNG), ("video", "video.mp4", VIDEO),
                                 ("document", "matricula.pdf", PDF), ("audio", "audio-data.wav", WAV)):
            with self.subTest(kind=kind):
                media = self.upload(kind, name, data, request="upload-" + kind)
                self.reply(media, request="reply-" + kind)
                gateway = EvolutionGateway()
                with patch.object(gateway, "request", return_value={"key": {"id": "accepted-" + kind}}) as request:
                    self.assertTrue(tick(self.store, gateway))
                    payload = request.call_args.args[2]
                    if kind == "audio":
                        self.assertIn("/message/sendText/", request.call_args.args[1])
                        self.assertEqual(payload["text"], "*Jane Equipe*")
                        self.assertTrue(tick(self.store, gateway))
                        payload = request.call_args.args[2]
                        self.assertIn("/message/sendWhatsAppAudio/", request.call_args.args[1])
                        self.assertNotIn("text", payload)
                        self.assertNotIn("caption", payload)
                        self.assertNotIn("fileName", payload)
                    else:
                        self.assertEqual(payload["caption"], "*Jane Equipe*")
                        self.assertEqual(payload["fileName"], name)
                thread = self.store.inbox(self.a, "operator")["conversations"][0]
                self.assertEqual(thread["media_kind"], kind)
                self.assertEqual(thread["body"], "*Jane Equipe*")

    def test_typed_attachment_captions_are_preserved_without_generated_text(self):
        for kind, name, data in (("image", "foto.png", PNG), ("video", "video.mp4", VIDEO),
                                 ("document", "matricula.pdf", PDF), ("audio", "recado.wav", WAV)):
            with self.subTest(kind=kind):
                media = self.upload(kind, name, data, request="upload-" + kind)
                key = self.reply(media, request="reply-" + kind, body="Segue o material solicitado.")
                with self.store.db() as c:
                    self.assertEqual(self.store.row(c, "messages", key)["body"], "*Jane Equipe*\n\nSegue o material solicitado.")
                gateway = Mock()
                gateway.send.return_value = "accepted-" + kind
                self.assertTrue(tick(self.store, gateway))
                self.assertEqual(gateway.send.call_args.args[0]["body"], "*Jane Equipe*\n\nSegue o material solicitado.")
                if kind == "audio":
                    self.assertTrue(tick(self.store, gateway))

    def test_pre_fix_attachment_request_retries_keep_original_message_without_resending(self):
        from crm_media import LABELS
        for kind, name, data in (("image", "foto.png", PNG), ("video", "video.mp4", VIDEO),
                                 ("document", "matricula.pdf", PDF), ("audio", "recado.wav", WAV)):
            with self.subTest(kind=kind):
                media = self.upload(kind, name, data, request="upload-" + kind)
                request = "reply-" + kind
                key = self.reply(media, request=request)
                legacy = f"*Jane Equipe*\n\n[{LABELS[kind]}] {name}"
                with self.store.db() as c:
                    c.execute("UPDATE messages SET body=? WHERE id=?", (legacy, key))
                    count = c.execute("SELECT count(*) FROM messages").fetchone()[0]
                self.assertEqual(self.reply(media, request=request), key)
                with self.assertRaises(Conflict):
                    self.reply(media, request=request, body="Outra legenda")
                with self.store.db() as c:
                    self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], count)
                    self.assertEqual(self.store.row(c, "messages", key)["body"], legacy)

    def test_audio_identification_precedes_audio_and_only_audio_resolves_reply(self):
        media = self.upload("audio", "recado.wav", WAV)
        key = self.reply(media, body="Orientações da equipe")
        gateway = Mock()
        gateway.send.side_effect = ["signature", "voice-note"]
        self.assertTrue(tick(self.store, gateway))
        first = gateway.send.call_args.args[0]
        self.assertFalse(first["media_id"])
        self.assertIn("*Jane Equipe*", first["body"])
        self.assertEqual(self.store.conversation(self.a, self.channel, self.contact)["unread"], 1)
        self.assertTrue(tick(self.store, gateway))
        last = gateway.send.call_args.args[0]
        self.assertEqual(last["id"], key)
        self.assertEqual(last["depends_on"], first["id"])
        self.assertEqual(last["media"]["kind"], "audio")
        self.assertEqual(self.store.conversation(self.a, self.channel, self.contact)["unread"], 0)

    def test_audio_does_not_follow_an_uncertain_signature_or_retry_automatically(self):
        media = self.upload("audio", "recado.wav", WAV)
        key = self.reply(media)
        gateway = Mock()
        gateway.send.side_effect = TimeoutError()
        self.assertTrue(tick(self.store, gateway))
        self.assertFalse(tick(self.store, gateway))
        gateway.send.assert_called_once()
        with self.store.db() as c:
            self.assertEqual(self.store.row(c, "messages", key)["status"], "queued")
        self.assertEqual(self.store.conversation(self.a, self.channel, self.contact)["unread"], 1)

    def test_media_cannot_be_used_by_another_user_contact_or_source_channel(self):
        media = self.upload()
        other = self.lead(number="5500000000003")
        contact = self.store.detail(other, self.a)["lead"]["contact_id"]
        for changes in ({"contact_id": contact}, {"doctor_id": self.b}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reply(media, **changes)
        with self.assertRaises(ValueError):
            self.store.reply({"doctor_id": self.a, "channel_id": self.channel, "contact_id": self.contact,
                "media_id": media["id"], "request_id": "foreign", "body": ""}, "other", True, sender_name="Outro")
        with self.assertRaises(ValueError):
            self.reply(media, body="x" * 1001)

    def test_optout_after_upload_and_before_dispatch_is_respected(self):
        media = self.upload()
        self.reply(media)
        self.store.activity({"id": self.key, "doctor_id": self.a, "kind": "optout", "body": "SAIR"}, "Equipe")
        gateway = Mock()
        self.assertFalse(tick(self.store, gateway))
        gateway.send.assert_not_called()
        with self.assertRaises(ValueError):
            self.upload(request="blocked")
        self.assertEqual(self.store.conversation(self.a, self.channel, self.contact)["unread"], 1)

    def test_tampered_or_missing_media_is_not_sent(self):
        media = self.upload()
        key = self.reply(media)
        path_for(self.store, media["id"]).write_bytes(b"changed")
        gateway = Mock()
        self.assertTrue(tick(self.store, gateway))
        gateway.send.assert_not_called()
        with self.store.db() as c:
            self.assertEqual(self.store.row(c, "messages", key)["status"], "unknown")
        self.assertFalse(tick(self.store, gateway))

    def test_media_echo_before_http_response_is_reconciled_without_duplicate(self):
        media = self.upload()
        key = self.reply(media)
        item = self.store.claim()
        self.store.receive(self.instance, {"key": {"id": "media-echo", "remoteJid": "5500000000002@s.whatsapp.net", "fromMe": True},
            "message": {"imageMessage": {"caption": item["body"]}}, "messageTimestamp": time.time()}, "messages.upsert")
        self.store.finish(item, "media-echo")
        messages = self.store.conversation(self.a, self.channel, self.contact)["messages"]
        self.assertEqual(len([m for m in messages if m["direction"] == "out"]), 1)
        self.assertEqual(messages[-1]["id"], key)
        self.assertEqual(messages[-1]["media"]["id"], media["id"])
        self.assertEqual(messages[-1]["sender_name"], "Jane Equipe")

    def test_gateway_uses_private_base64_and_correct_evolution_endpoints(self):
        gateway = EvolutionGateway()
        item = {"instance": "instance/test", "phone": "5511999990001", "body": "*Equipe*\n\nLegenda"}
        for kind, suffix in (("image", "sendMedia"), ("video", "sendMedia"), ("document", "sendMedia"), ("audio", "sendWhatsAppAudio")):
            media = {"kind": kind, "mime": "audio/wav" if kind == "audio" else "application/pdf", "name": "arquivo", "base64": "Ynl0ZXM="}
            with self.subTest(kind=kind), patch.object(gateway, "request", return_value={"key": {"id": "accepted"}}) as request:
                self.assertEqual(gateway.send({**item, "media": media}), "accepted")
                self.assertEqual(request.call_args.args[1], "/message/" + suffix + "/instance%2Ftest")
                payload = request.call_args.args[2]
                self.assertEqual(payload["number"], item["phone"])
                if kind == "audio":
                    self.assertEqual(payload["audio"], media["base64"])
                    self.assertIs(payload["encoding"], True)
                else:
                    self.assertEqual(payload["caption"], item["body"])
                    self.assertEqual(payload["media"], media["base64"])

    def handler(self, method="GET", master=True, user="operator", origin=True, raw=b"", headers=None):
        h = Mock()
        h.current_user = {"id": user, "nome": "Equipe", "is_master": master}
        h.command = method
        h.headers = {"Content-Length": str(len(raw)), **(headers or {})}
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.same_origin_request.return_value = origin
        return h

    def test_upload_api_requires_master_same_origin_and_owned_conversation(self):
        query = {"clinic": "vielle", "doctor": self.a, "channel": self.channel, "contact": self.contact, "kind": "image"}
        path = "/api/crm/media-upload?" + urlencode(query)
        headers = {"X-File-Name": "teste.png", "X-Upload-Id": "http-upload"}
        with patch("crm_api.store_for", return_value=self.store):
            for options in ({"master": False}, {"origin": False}):
                h = self.handler("POST", raw=PNG, headers=headers, **options)
                handle_request(h, urlparse(path))
                self.assertEqual(h.auth_json.call_args.args[1], 403)
            h = self.handler("POST", raw=PNG, headers=headers)
            handle_request(h, urlparse(path))
            result = h.auth_json.call_args.args[0]
            self.assertTrue(result["ok"])
            self.assertEqual(result["media"]["size"], len(PNG))
            h.server.auth_store.audit_event.assert_called_once()

    def test_downloads_are_private_scoped_seekable_and_safe(self):
        media = self.upload()
        h = self.handler(headers={"Range": "bytes=2-9"})
        serve(h, self.store, self.a, media["id"])
        self.assertEqual(h.send_response.call_args.args[0], 206)
        self.assertEqual(h.wfile.getvalue(), PNG[2:10])
        response_headers = dict(call.args for call in h.send_header.call_args_list)
        self.assertEqual(response_headers["Cache-Control"], "private, no-store")
        self.assertEqual(response_headers["X-Content-Type-Options"], "nosniff")
        with self.assertRaises(ValueError):
            serve(self.handler(user="other"), self.store, self.a, media["id"])
        with self.assertRaises(ValueError):
            serve(self.handler(), self.store, self.b, media["id"])
        h = self.handler(headers={"Range": "bytes=999-"})
        serve(h, self.store, self.a, media["id"])
        self.assertEqual(h.send_response.call_args.args[0], 416)
        document = self.upload("document", "orientacoes.pdf", PDF, request="doc")
        h = self.handler()
        serve(h, self.store, self.a, document["id"])
        self.assertTrue(dict(call.args for call in h.send_header.call_args_list)["Content-Disposition"].startswith("attachment;"))

    def test_old_drafts_expire_but_bound_history_is_retained(self):
        bound = self.upload()
        self.reply(bound)
        orphan = self.upload("document", "rascunho.pdf", PDF, request="orphan")
        with self.store.db() as c:
            c.execute("UPDATE message_media SET created_at=?", (time.time() - 90000,))
        self.upload("audio", "novo.wav", WAV, request="new")
        self.assertFalse(path_for(self.store, orphan["id"]).exists())
        self.assertTrue(path_for(self.store, bound["id"]).exists())
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM message_media").fetchone()[0], 2)

    def received(self, kind="image", *, message_key="received-media", **changes):
        field = {"image": "imageMessage", "audio": "audioMessage", "video": "videoMessage", "document": "documentMessage"}[kind]
        mime = {"image": "image/png", "audio": "audio/wav", "video": "video/mp4", "document": "application/pdf"}[kind]
        payload = {"key": {"id": message_key, "remoteJid": "5500000000002@s.whatsapp.net", "fromMe": False},
            "message": {field: {"mimetype": mime, "fileName": "arquivo.pdf" if kind == "document" else "anexo", "fileLength": 42,
                "url": "https://private.whatsapp.invalid/media", "mediaKey": "private-key", "directPath": "/private/path"}},
            "messageTimestamp": time.time(), **changes}
        self.assertTrue(self.store.receive(self.instance, payload, "messages.upsert"))
        message = self.store.conversation(self.a, self.channel, self.contact)["messages"][-1]
        return payload, message

    def test_all_received_types_keep_provider_keys_private_and_cache_on_demand(self):
        for kind, data in (("image", PNG), ("audio", WAV), ("video", VIDEO), ("document", PDF)):
            with self.subTest(kind=kind):
                payload, message = self.received(kind, message_key=kind)
                media = message["media"]
                self.assertEqual(media["kind"], kind)
                self.assertEqual(media["state"], "remote")
                self.assertFalse(path_for(self.store, media["id"]).exists())
                serialized = json.dumps(message)
                for private in ("private-key", "whatsapp.invalid", "directPath", "provider_payload"):
                    self.assertNotIn(private, serialized)
                gateway = Mock()
                gateway.download_media.return_value = {"base64": base64.b64encode(data).decode(),
                    "mimetype": next(iter(payload["message"].values()))["mimetype"], "fileName": "ficha.pdf"}
                ready = fetch_media(self.store, gateway, self.a, media["id"])
                self.assertEqual(ready["state"], "ready")
                self.assertEqual(path_for(self.store, media["id"]).read_bytes(), data)
                self.assertEqual(ready, fetch_media(self.store, gateway, self.a, media["id"]))
                gateway.download_media.assert_called_once()
                gateway.media_message.assert_not_called()
                with self.store.db() as c:
                    self.assertEqual(self.store.row(c, "message_media", media["id"])["provider_payload"], "")
        self.assertEqual(self.store.conversation(self.a, self.channel, self.contact)["unread"], 5)

    def test_legacy_placeholder_fetches_exact_message_without_marking_read(self):
        payload, message = self.received()
        with self.store.db() as c:
            c.execute("DELETE FROM message_media WHERE id=?", (message["media"]["id"],))
            c.execute("UPDATE messages SET media_id='' WHERE id=?", (message["id"],))
        legacy = self.store.conversation(self.a, self.channel, self.contact)["messages"][-1]["media"]
        gateway = Mock()
        gateway.media_message.return_value = payload
        gateway.download_media.return_value = {"base64": base64.b64encode(PNG).decode(), "mimetype": "image/png"}
        self.assertEqual(fetch_media(self.store, gateway, self.a, legacy["id"])["state"], "ready")
        self.assertEqual(gateway.media_message.call_args.args[1], payload["key"]["id"])
        self.assertEqual(self.store.conversation(self.a, self.channel, self.contact)["unread"], 2)

    def test_history_enriches_existing_inbound_media_without_new_unread(self):
        payload, message = self.received()
        with self.store.db() as c:
            c.execute("UPDATE message_media SET provider_payload=? WHERE id=?", (json.dumps({"key": payload["key"]}), message["media"]["id"]))
        self.assertFalse(self.store.receive(self.instance, payload, "messages.upsert", expected_contact=self.contact, history=True))
        with self.store.db() as c:
            saved = self.store.row(c, "message_media", message["media"]["id"])
            self.assertIn("message", json.loads(saved["provider_payload"]))
        unseen = {**payload, "key": {**payload["key"], "id": "old-unseen-inbound"}}
        self.assertFalse(self.store.receive(self.instance, unseen, "messages.upsert", expected_contact=self.contact, history=True))
        self.assertEqual(self.store.conversation(self.a, self.channel, self.contact)["unread"], 2)

    def test_external_outgoing_media_is_visible(self):
        _, message = self.received("audio", **{"key": {"id": "external-audio", "remoteJid": "5500000000002@s.whatsapp.net", "fromMe": True}})
        self.assertEqual(message["direction"], "out")
        self.assertEqual(message["origin"], "external")
        self.assertEqual(message["media"]["kind"], "audio")

    def test_view_once_is_not_downloaded_or_archived(self):
        for wrapper in ("viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension"):
            self.assertIsNone(extract({"message": {wrapper: {"message": {"imageMessage": {"mediaKey": "private"}}}}}))
        self.assertIsNone(extract({"message": {"imageMessage": {"viewOnce": True}}}))
        payload, message = self.received()
        with self.store.db() as c:
            c.execute("UPDATE message_media SET provider_payload=? WHERE id=?", (json.dumps({"key": payload["key"]}), message["media"]["id"]))
        gateway = Mock()
        gateway.media_message.return_value = {**payload, "message": {"viewOnceMessageV2": {"message": payload["message"]}}}
        with self.assertRaisesRegex(ValueError, "visualizacao unica"):
            fetch_media(self.store, gateway, self.a, message["media"]["id"])
        gateway.download_media.assert_not_called()

    def test_received_download_rejects_invalid_large_or_foreign_media(self):
        payload, message = self.received()
        media = message["media"]
        gateway = Mock()
        for response in ({"base64": "not-base64!"}, {"base64": ""}, {"base64": "YQ==", "mimetype": "text/html"},
                         {"base64": "YQ==", "mimetype": "image/png"}):
            gateway.download_media.return_value = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                fetch_media(self.store, gateway, self.a, media["id"])
            self.assertFalse(path_for(self.store, media["id"]).exists())
        with self.assertRaises(ValueError):
            fetch_media(self.store, gateway, self.b, media["id"])
        gateway.reset_mock()
        with self.store.db() as c:
            c.execute("UPDATE message_media SET provider_payload=? WHERE id=?", (json.dumps({"key": payload["key"]}), media["id"]))
        gateway.media_message.return_value = {**payload, "key": {**payload["key"], "remoteJid": "5500000000003@s.whatsapp.net"}}
        with self.assertRaisesRegex(ValueError, "outra conversa"):
            fetch_media(self.store, gateway, self.a, media["id"])
        gateway.download_media.assert_not_called()

    def test_received_device_jid_is_normalized(self):
        payload, message = self.received(**{"key": {"id": "device", "remoteJid": "5500000000002:1@s.whatsapp.net"}})
        gateway = Mock()
        gateway.download_media.return_value = {"base64": base64.b64encode(PNG).decode(), "mimetype": "image/png"}
        self.assertEqual(fetch_media(self.store, gateway, self.a, message["media"]["id"])["state"], "ready")

    def test_fetch_api_requires_same_origin_master_and_matching_doctor(self):
        _, message = self.received()
        path = urlparse("/api/crm/media-fetch?clinic=vielle&doctor=" + self.a)
        payload = {"doctor_id": self.a, "id": message["media"]["id"]}
        with patch("crm_api.store_for", return_value=self.store), patch("crm_media.fetch_media", return_value=message["media"]) as fetch:
            for options in ({"master": False}, {"origin": False}):
                h = self.handler("POST", raw=json.dumps(payload).encode(), **options)
                handle_request(h, path)
                self.assertEqual(h.auth_json.call_args.args[1], 403)
                fetch.assert_not_called()
            h = self.handler("POST", raw=json.dumps({**payload, "doctor_id": self.b}).encode())
            handle_request(h, path)
            self.assertEqual(h.auth_json.call_args.args[1], 400)
            fetch.assert_not_called()
            h = self.handler("POST", raw=json.dumps(payload).encode())
            handle_request(h, path)
            self.assertEqual(h.auth_json.call_args.args[0]["media"], message["media"])
            fetch.assert_called_once()

    def test_gateway_download_is_bounded_and_uses_key_lookup(self):
        gateway = EvolutionGateway()
        payload = {"key": {"id": "exact"}, "message": {"audioMessage": {}}}
        with patch.object(gateway, "request", return_value={"messages": {"records": [payload]}}) as request:
            self.assertEqual(gateway.media_message({"instance": "test/a"}, "exact"), payload)
            self.assertEqual(request.call_args.args[2]["where"]["key"]["id"], "exact")
        with patch.object(gateway, "request", return_value={"base64": "YQ=="}) as request:
            gateway.download_media({"instance": "test/a"}, payload, 100)
            self.assertEqual(request.call_args.args[1], "/chat/getBase64FromMediaMessage/test%2Fa")
            self.assertEqual(request.call_args.args[2]["message"], payload)
            self.assertLess(request.call_args.kwargs["maximum"], 40000)


if __name__ == "__main__":
    unittest.main()
