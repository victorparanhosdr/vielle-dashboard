import base64
import json
from io import BytesIO
import unittest
from unittest.mock import Mock

from crm_access import crm_access
from crm_media import extract, fetch_media, path_for, serve
from tests import test_crm_media as base
from tests.crm_sticker_fixture import STATIC, ANIMATED, payload


class StickerTests(unittest.TestCase):
    setUp = base.MediaTests.setUp
    lead = base.MediaTests.lead
    handler = base.MediaTests.handler

    def receive(self, value=None):
        value = value or payload()
        self.store.receive(self.instance,value,"messages.upsert")
        return self.store.conversation(self.a,self.channel,self.contact)["messages"][-1]

    def gateway(self, raw=STATIC):
        gateway=Mock()
        gateway.download_media.return_value={"base64":base64.b64encode(raw).decode(),"mimetype":"image/webp"}
        return gateway

    def test_inbound_sticker_is_private_image_with_persistent_marker_and_cache(self):
        message=self.receive()
        self.assertEqual(message["body"],"[Figurinha]")
        media=message["media"]
        self.assertEqual(media["kind"],"image")
        self.assertTrue(media["is_sticker"])
        self.assertEqual(media["name"],"figurinha.webp")
        self.assertEqual(media["state"],"remote")
        self.assertNotIn("provider.invalid",json.dumps(message))
        self.assertNotIn("mediaKey",json.dumps(message))
        gateway=self.gateway()
        ready=fetch_media(self.store,gateway,self.a,media["id"])
        self.assertTrue(ready["is_sticker"])
        self.assertEqual(ready["mime"],"image/webp")
        self.assertEqual(path_for(self.store,media["id"]).read_bytes(),STATIC)
        self.assertEqual(ready,fetch_media(self.store,gateway,self.a,media["id"]))
        gateway.download_media.assert_called_once()
        self.assertEqual(gateway.download_media.call_args.args[-1],1024*1024)
        self.assertEqual(self.store.conversation(self.a,self.channel,self.contact)["unread"],2)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE direction='out'").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT provider_payload FROM message_media WHERE id=?",(media["id"],)).fetchone()[0],"")

    def test_animated_webp_bytes_and_private_content_type_are_preserved(self):
        from PIL import Image
        with Image.open(BytesIO(STATIC)) as image:self.assertEqual(image.size,(128,128))
        with Image.open(BytesIO(ANIMATED)) as image:self.assertEqual(image.n_frames,2)
        self.assertIn(b"ANIM",ANIMATED)
        message=self.receive(payload(animated=True))
        media=fetch_media(self.store,self.gateway(ANIMATED),self.a,message["media"]["id"])
        h=self.handler()
        serve(h,self.store,self.a,media["id"])
        self.assertEqual(h.wfile.getvalue(),ANIMATED)
        headers=dict(call.args for call in h.send_header.call_args_list)
        self.assertEqual(headers["Content-Type"],"image/webp")
        self.assertEqual(headers["Cache-Control"],"private, no-store")

    def test_old_placeholder_uses_exact_message_lookup_without_new_unread(self):
        value=payload("old-sticker")
        self.store.receive(self.instance,{**value,"message":{"conversation":"[Figurinha]"}},"messages.upsert")
        message=self.store.conversation(self.a,self.channel,self.contact)["messages"][-1]
        self.assertTrue(message["media"]["is_sticker"])
        gateway=self.gateway();gateway.media_message.return_value=value
        ready=fetch_media(self.store,gateway,self.a,message["media"]["id"])
        self.assertEqual(ready["state"],"ready")
        self.assertEqual(gateway.media_message.call_args.args[1],"old-sticker")
        self.assertEqual(self.store.conversation(self.a,self.channel,self.contact)["unread"],2)
        self.assertFalse(self.store.receive(self.instance,value,"messages.upsert",expected_contact=self.contact,history=True))
        self.assertEqual(self.store.conversation(self.a,self.channel,self.contact)["unread"],2)

    def test_wrappers_supported_but_view_once_never_downloaded(self):
        raw=payload()["message"]
        nested={"message":{"ephemeralMessage":{"message":raw}}}
        self.assertIn("stickerMessage",extract(nested)[2])
        for key in ("viewOnceMessage","viewOnceMessageV2","viewOnceMessageV2Extension"):
            self.assertIsNone(extract({"message":{key:{"message":raw}}}))
        self.assertIsNone(extract({"message":{"stickerMessage":{"viewOnce":True}}}))

    def test_foreign_user_scope_or_conversation_cannot_download(self):
        message=self.receive();media=message["media"]
        gateway=self.gateway()
        with self.assertRaises(ValueError):fetch_media(self.store,gateway,self.b,media["id"])
        with crm_access({self.a:[]},self.a), self.assertRaises(PermissionError):
            fetch_media(self.store,gateway,self.a,media["id"])
        gateway.download_media.assert_not_called()
        with self.store.db() as c:
            c.execute("UPDATE message_media SET provider_payload=? WHERE id=?",(json.dumps({"key":payload()["key"]}),media["id"]))
        foreign=payload();foreign["key"]["remoteJid"]="5500000000003@s.whatsapp.net"
        gateway.media_message.return_value=foreign
        with self.assertRaisesRegex(ValueError,"outra conversa"):
            fetch_media(self.store,gateway,self.a,media["id"])
        gateway.download_media.assert_not_called()

    def test_invalid_format_oversize_and_changed_type_fail_without_cached_file(self):
        media=self.receive()["media"]
        for raw,mime in ((base.PNG,"image/png"),(base.PNG,"image/webp"),(STATIC+b"x"*(1024*1024),"image/webp")):
            gateway=self.gateway(raw);gateway.download_media.return_value["mimetype"]=mime
            with self.subTest(mime=mime),self.assertRaises(ValueError):
                fetch_media(self.store,gateway,self.a,media["id"])
            self.assertFalse(path_for(self.store,media["id"]).exists())
        with self.store.db() as c:
            c.execute("UPDATE message_media SET provider_payload=? WHERE id=?",(json.dumps({"key":payload()["key"]}),media["id"]))
        gateway=self.gateway();changed=payload();changed["message"]={"imageMessage":{"mimetype":"image/webp"}}
        gateway.media_message.return_value=changed
        with self.assertRaisesRegex(ValueError,"corresponde"):
            fetch_media(self.store,gateway,self.a,media["id"])
        gateway.download_media.assert_not_called()

    def test_sticker_without_mime_can_use_validated_webp_fallback(self):
        value=payload();value["message"]["stickerMessage"].pop("mimetype")
        message=self.receive(value)
        gateway=self.gateway();gateway.download_media.return_value["mimetype"]="application/octet-stream"
        self.assertEqual(fetch_media(self.store,gateway,self.a,message["media"]["id"])["mime"],"image/webp")


if __name__ == "__main__":unittest.main()
