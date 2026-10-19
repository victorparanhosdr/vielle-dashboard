from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from crm_store import CrmStore, Conflict
from crm_voice import VoiceConfig, VoiceService, MAX_AUDIO, TwilioVoice


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CrmStore(Path(self.temp.name))
        self.store.bootstrap({"A": ["Organico"], "B": ["Organico"]})
        self.doc, self.other = [d["id"] for d in self.store.listing()["doctors"]]
        p = self.store.listing(self.doc)["pipelines"][0]
        self.lead = self.store.lead({"doctor_id":self.doc,"pipeline_id":p["id"],"stage_id":p["stages"][0]["id"],
            "name":"Cliente Exemplo","phone":"5511999990001","title":"Retorno","consent":True,"consent_source":"Solicitado pelo paciente"}, "Equipe")
        self.actor = {"id":"user-1","nome":"Equipe Vielle"}
        self.config = VoiceConfig("AC"+"1"*32,"t"*32,"SK"+"2"*32,"s"*32,"AP"+"3"*32,"https://example.test",
            ({"id":"line-a","doctor_id":self.doc,"name":"Atendimento","number":"+551140000001"},), True)
        self.config.notice_mode="automated"
        self.gateway = Mock()
        self.gateway.owns_number.return_value = True
        self.gateway.token.return_value = "short-lived-token"
        self.gateway.record.return_value = "RE"+"4"*32
        self.gateway.audio.return_value = b"ID3 synthetic audio"
        self.voice = VoiceService(self.store,self.config,self.gateway)
        self.payload = {"doctor_id":self.doc,"lead_id":self.lead,"line_id":"line-a","request_id":"a"*32}
        self.parent, self.child = "CA"+"5"*32, "CA"+"6"*32

    def start(self):
        key = self.voice.prepare(self.payload,self.actor)["call"]["id"]
        xml = self.voice.callback("twiml",key,{"AccountSid":self.config.account,"From":"client:crm-call-"+key,"CallSid":self.parent,"To":"+19009000000"})
        return key,xml

    def params(self, **changes):
        return {"AccountSid":self.config.account,"CallSid":self.child,"ParentCallSid":self.parent,**changes}

    def complete(self):
        key,_=self.start()
        self.voice.callback("consent",key,self.params(Digits="1"))
        self.voice.callback("recording",key,{"AccountSid":self.config.account,"CallSid":self.parent,"RecordingSid":"RE"+"4"*32,"RecordingStatus":"completed","RecordingDuration":"65","RecordingUrl":"https://evil.test/steal"})
        return key

    def test_disabled_by_default_and_configuration_contains_no_secrets(self):
        with patch.dict("os.environ",{},clear=True):
            cfg=VoiceConfig.from_env()
        self.assertFalse(cfg.status(self.doc)["enabled"])
        exposed=json.dumps(self.config.status(self.doc))
        self.assertNotIn(self.config.auth_token,exposed)
        self.assertNotIn(self.config.api_secret,exposed)
        self.config.enabled=False
        with self.assertRaises(ValueError):self.voice.prepare(self.payload,self.actor)
        self.gateway.owns_number.assert_not_called()

    def test_prepare_is_atomic_idempotent_owned_and_limited(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls=list(pool.map(lambda _:self.voice.prepare(self.payload,self.actor)["call"],range(2)))
        self.assertEqual(calls[0]["id"],calls[1]["id"])
        with self.assertRaises(Conflict):self.voice.prepare({**self.payload,"request_id":"b"*32},self.actor)
        with self.assertRaises(ValueError):self.voice.prepare({**self.payload,"doctor_id":self.other},self.actor)
        self.gateway.owns_number.return_value=False
        with self.assertRaises(ValueError):self.voice.prepare(self.payload,self.actor)

    def test_optout_and_identity_checks(self):
        with self.store.db() as c:c.execute("UPDATE contacts SET opted_out=1")
        with self.assertRaises(ValueError):self.voice.prepare(self.payload,self.actor)
        with self.store.db() as c:
            c.execute("UPDATE contacts SET opted_out=0,experts_status='review'")
        with self.assertRaises(ValueError):self.voice.prepare(self.payload,self.actor)

    def test_manual_call_without_individual_contact_consent_does_not_invent_consent(self):
        with self.store.db() as c:
            c.execute("UPDATE contacts SET experts_status='new'")
            c.execute("UPDATE leads SET consent_at=NULL,consent_source=''")
        key,xml=self.start()
        self.assertIn("<Dial",xml)
        self.gateway.record.assert_not_called()
        with self.store.db() as c:
            lead=c.execute("SELECT consent_at,consent_source FROM leads WHERE id=?",(self.lead,)).fetchone()
            self.assertIsNone(lead["consent_at"])
            self.assertEqual(lead["consent_source"],"")
            self.assertIsNone(self.voice.row(c,key)["consent_at"])
        self.voice.callback("consent",key,self.params(Digits="2"))
        self.gateway.record.assert_not_called()

    def test_optout_or_identity_review_after_preparation_prevents_dial(self):
        for change in ("opted_out=1", "experts_status='review'"):
            with self.subTest(change=change):
                with self.store.db() as c:
                    c.execute("UPDATE contacts SET opted_out=0,experts_status='matched'")
                key=self.voice.prepare(self.payload,self.actor)["call"]["id"]
                with self.store.db() as c:c.execute("UPDATE contacts SET "+change)
                xml=self.voice.callback("twiml",key,{"AccountSid":self.config.account,"From":"client:crm-call-"+key,"CallSid":self.parent})
                self.assertNotIn("<Dial",xml)
                self.assertIn("<Hangup",xml)
                self.voice.cancel({"id":key,"doctor_id":self.doc},self.actor)
                self.payload["request_id"]="b"*32

    def test_invalid_destination_does_not_prepare_call(self):
        with self.store.db() as c:c.execute("UPDATE contacts SET phone='5511'")
        with self.assertRaisesRegex(ValueError,"telefone brasileiro"):
            self.voice.prepare(self.payload,self.actor)

    def test_twiml_ignores_client_destination_and_replays_do_not_dial(self):
        key,xml=self.start()
        self.assertIn("+5511999990001",xml)
        self.assertNotIn("+19009000000",xml)
        self.assertNotIn("record=",xml)
        self.assertIn('timeLimit="1200"',xml)
        repeat=self.voice.callback("twiml",key,{"AccountSid":self.config.account,"From":"client:crm-call-"+key,"CallSid":self.parent})
        self.assertNotIn("<Dial",repeat)
        self.gateway.record.assert_not_called()

    def test_customer_notice_precedes_recording_and_requires_one(self):
        key,_=self.start()
        xml=self.voice.callback("notice",key,self.params())
        self.assertIn("Gather",xml)
        self.assertIn("pressione 1",xml)
        self.gateway.record.assert_not_called()
        self.voice.callback("consent",key,self.params(Digits="2"))
        self.gateway.record.assert_not_called()
        self.assertEqual(self.voice.history(self.lead,self.doc)["calls"][0]["status"],"declined")

    def staff_call(self):
        self.config.notice_mode="staff"
        key,xml=self.start()
        self.voice.callback("status",key,self.params(CallStatus="in-progress"))
        return key,xml

    def authorize_recording(self,key,**changes):
        return self.voice.start_recording({"id":key,"doctor_id":self.doc,"verbal_authorization":True,**changes},self.actor)

    def test_staff_notice_call_connects_directly_without_recording_or_robot(self):
        from xml.etree import ElementTree
        key,xml=self.staff_call()
        dial=ElementTree.fromstring(xml).find("Dial")
        self.assertNotIn("record",dial.attrib)
        self.assertNotIn("url",dial.find("Number").attrib)
        self.assertNotIn("<Say",xml)
        self.assertNotIn("<Gather",xml)
        self.gateway.record.assert_not_called()
        row=self.voice.history(self.lead,self.doc)["calls"][0]
        self.assertEqual((row["notice_mode"],row["consent_source"]),("staff",""))
        self.assertIsNone(row["consent_at"])
        self.assertIsNotNone(row["answered_at"])
        with self.assertRaises(ValueError):self.voice.callback("consent",key,self.params(Digits="1"))
        with self.assertRaises(ValueError):self.voice.callback("recording",key,{"AccountSid":self.config.account,"CallSid":self.parent,"RecordingSid":"RE"+"4"*32,"RecordingStatus":"completed"})

    def test_staff_recording_requires_fresh_explicit_attestation_for_this_call(self):
        key,_=self.staff_call()
        for value in (None,False,1,"true"):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.authorize_recording(key,verbal_authorization=value)
        self.gateway.record.assert_not_called()
        row=self.authorize_recording(key)["call"]
        self.assertEqual(row["consent_source"],"staff_verbal")
        self.assertIsNotNone(row["consent_at"])
        self.assertEqual(row["recording_status"],"recording")
        self.assertEqual(row["status"],"in-progress")
        self.gateway.record.assert_called_once_with(self.parent,key)
        events=self.store.detail(self.lead,self.doc)["events"]
        event=next(e for e in events if e["kind"]=="call_recording_authorization")
        self.assertEqual(event["actor"],self.actor["nome"])
        self.assertEqual(json.loads(event["body"])["call_id"],key)
        self.assertEqual(self.store.detail(self.lead,self.doc)["lead"]["consent_source"],"Solicitado pelo paciente")

    def test_staff_recording_is_owned_connected_and_bound_to_current_contact(self):
        key,_=self.staff_call()
        payload={"id":key,"doctor_id":self.doc,"verbal_authorization":True}
        with self.assertRaises(ValueError):self.voice.start_recording(payload,{**self.actor,"id":"other-user"})
        with self.assertRaises(LookupError):self.voice.start_recording({**payload,"doctor_id":self.other},self.actor)
        with self.store.db() as c:c.execute("UPDATE voice_calls SET status='ringing'")
        with self.assertRaises(ValueError):self.voice.start_recording(payload,self.actor)
        with self.store.db() as c:c.execute("UPDATE voice_calls SET status='in-progress'")
        for change in ("opted_out=1", "experts_status='review'", "phone='5511999990002'"):
            with self.subTest(change=change):
                with self.store.db() as c:
                    c.execute("UPDATE contacts SET opted_out=0,experts_status='matched',phone='5511999990001'")
                    c.execute("UPDATE contacts SET "+change)
                with self.assertRaises(ValueError):self.voice.start_recording(payload,self.actor)
        self.gateway.record.assert_not_called()

    def test_staff_recording_concurrent_requests_start_only_once(self):
        key,_=self.staff_call()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:self.authorize_recording(key),range(2)))
        self.authorize_recording(key)
        self.gateway.record.assert_called_once_with(self.parent,key)

    def test_staff_recording_failure_keeps_conversation_and_never_retries_automatically(self):
        key,_=self.staff_call()
        self.gateway.record.side_effect=TimeoutError()
        row=self.authorize_recording(key)["call"]
        self.assertEqual((row["status"],row["recording_status"]),("in-progress","error"))
        self.authorize_recording(key)
        self.gateway.record.assert_called_once_with(self.parent,key)
        self.gateway.hangup.assert_not_called()

    def test_staff_authorized_audio_uses_the_same_private_archive(self):
        key,_=self.staff_call()
        self.authorize_recording(key)
        self.voice.callback("recording",key,{"AccountSid":self.config.account,"CallSid":self.parent,"RecordingSid":"RE"+"4"*32,"RecordingStatus":"completed","RecordingDuration":"20"})
        self.assertTrue(self.voice.archive_one())
        self.assertEqual(self.voice.audio_path(key,self.doc).stat().st_mode&0o777,0o600)
        self.assertEqual(self.voice.history(self.lead,self.doc)["calls"][0]["consent_source"],"staff_verbal")

    def test_staff_authorization_is_not_reused_for_next_call(self):
        key,_=self.staff_call()
        self.authorize_recording(key)
        self.voice.callback("status",key,self.params(CallStatus="completed"))
        self.payload["request_id"]="b"*32
        row=self.voice.prepare(self.payload,self.actor)["call"]
        self.assertIsNone(row["consent_at"])
        self.assertEqual(row["consent_source"],"")
        self.assertEqual(row["recording_status"],"waiting")

    def test_notice_mode_is_per_call_and_migration_preserves_history(self):
        key,_=self.start()
        self.voice.callback("consent",key,self.params(Digits="1"))
        previous=self.voice.history(self.lead,self.doc)["calls"][0]
        with self.store.db() as c:
            c.execute("ALTER TABLE voice_calls DROP COLUMN notice_mode")
            c.execute("ALTER TABLE voice_calls DROP COLUMN consent_source")
        self.config.notice_mode="staff"
        with ThreadPoolExecutor(max_workers=2) as pool:
            services=list(pool.map(lambda _:VoiceService(self.store,self.config,self.gateway),range(2)))
        row=services[0].history(self.lead,self.doc)["calls"][0]
        self.assertEqual((row["notice_mode"],row["consent_source"]),("automated","patient_keypad"))
        self.assertEqual(row["consent_at"],previous["consent_at"])

    def test_staff_is_default_and_unknown_configuration_keeps_automated_notice(self):
        with patch.dict("os.environ",{},clear=True):self.assertEqual(VoiceConfig.from_env().notice_mode,"staff")
        with patch.dict("os.environ",{"CRM_VOICE_NOTICE_MODE":"invalid"},clear=True):
            self.assertEqual(VoiceConfig.from_env().notice_mode,"automated")

    def test_recording_once_only_after_consent(self):
        key,_=self.start()
        self.voice.callback("consent",key,self.params(Digits="1"))
        self.voice.callback("consent",key,self.params(Digits="1"))
        self.gateway.record.assert_called_once_with(self.parent,key)
        row=self.voice.history(self.lead,self.doc)["calls"][0]
        self.assertIsNotNone(row["consent_at"])
        self.assertEqual(row["recording_status"],"recording")

    def test_recording_failure_fails_closed(self):
        self.gateway.record.side_effect=TimeoutError()
        key,_=self.start()
        xml=self.voice.callback("consent",key,self.params(Digits="1"))
        self.assertIn("Hangup",xml)
        self.voice.callback("finished",key,{"AccountSid":self.config.account,"CallSid":self.parent,"DialCallStatus":"completed"})
        self.assertEqual(self.voice.history(self.lead,self.doc)["calls"][0]["status"],"failed")

    def test_callbacks_bind_account_parent_child_and_recording(self):
        key,_=self.start()
        with self.assertRaises(ValueError):self.voice.callback("status",key,self.params(ParentCallSid="CA"+"9"*32,CallStatus="completed"))
        with self.assertRaises(ValueError):self.voice.callback("status",key,self.params(AccountSid="AC"+"9"*32,CallStatus="completed"))
        with self.assertRaises(ValueError):self.voice.callback("recording",key,{"AccountSid":self.config.account,"CallSid":self.parent,"RecordingSid":"RE"+"4"*32,"RecordingStatus":"completed"})
        self.voice.callback("status",key,self.params(CallStatus="ringing"))
        self.voice.callback("status",key,self.params(CallStatus="initiated"))
        self.assertEqual(self.voice.history(self.lead,self.doc)["calls"][0]["status"],"ringing")
        with self.assertRaises(ValueError):self.voice.callback("status",key,self.params(CallSid="CA"+"9"*32,CallStatus="completed"))

    def test_archive_private_idempotent_and_contact_history_not_just_lead(self):
        key=self.complete()
        self.assertTrue(self.voice.archive_one())
        self.assertFalse(self.voice.archive_one())
        path=self.voice.audio_path(key,self.doc)
        self.assertEqual(path.read_bytes(),self.gateway.audio.return_value)
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        with self.store.db() as c:
            row=self.voice.row(c,key)
            self.assertEqual(row["audio_hash"],hashlib.sha256(path.read_bytes()).hexdigest())
        self.voice.callback("recording",key,{"AccountSid":self.config.account,"CallSid":self.parent,"RecordingSid":"RE"+"4"*32,"RecordingStatus":"completed","RecordingDuration":"65"})
        self.assertEqual(self.voice.history(self.lead,self.doc)["calls"][0]["recording_status"],"stored")
        with self.assertRaises(LookupError):self.voice.audio_path(key,self.other)
        public=json.dumps(self.voice.history(self.lead,self.doc))
        self.assertNotIn("RecordingUrl",public)
        self.assertNotIn(self.parent,public)

    def test_archive_retries_safely_then_offers_manual_retry(self):
        key=self.complete()
        self.gateway.audio.side_effect=TimeoutError()
        for _ in range(5):
            with self.store.db() as c:c.execute("UPDATE voice_calls SET retry_at=0")
            self.voice.archive_one()
        row=self.voice.history(self.lead,self.doc)["calls"][0]
        self.assertEqual(row["recording_status"],"error")
        self.assertTrue(row["can_retry_recording"])
        self.assertFalse(self.voice.archive_one())
        self.voice.retry_recording({"id":key,"doctor_id":self.doc})
        self.assertEqual(self.voice.history(self.lead,self.doc)["calls"][0]["recording_status"],"queued")

    def test_missing_callback_recovered_by_trusted_recording_id(self):
        key,_=self.start()
        self.voice.callback("consent",key,self.params(Digits="1"))
        with self.store.db() as c:c.execute("UPDATE voice_calls SET created_at=?",(time.time()-5000,))
        self.gateway.recording_state.return_value={"status":"completed","duration":"10","call_sid":self.parent}
        self.voice.recover_one()
        self.assertTrue(self.voice.archive_one())

    def test_notes_audited_and_cancel_owned(self):
        key,_=self.start()
        self.voice.save_note({"id":key,"doctor_id":self.doc,"note":"Retornar na sexta","outcome":"return"},self.actor)
        with self.assertRaises(LookupError):self.voice.save_note({"id":key,"doctor_id":self.other,"note":""},self.actor)
        with self.assertRaises(ValueError):self.voice.cancel({"id":key,"doctor_id":self.doc},{**self.actor,"id":"other"})
        self.voice.cancel({"id":key,"doctor_id":self.doc},self.actor)
        self.gateway.hangup.assert_called_once_with(self.parent)
        self.assertTrue(any(e["kind"]=="call_note" for e in self.store.detail(self.lead,self.doc)["events"]))

    def test_sdk_token_is_short_lived_and_outbound_only(self):
        import base64
        token=TwilioVoice(self.config).token("a"*32)
        data=json.loads(base64.urlsafe_b64decode(token.split(".")[1]+"=="))
        self.assertEqual(data["grants"]["identity"],"crm-call-"+"a"*32)
        self.assertNotIn("incoming",data["grants"]["voice"])
        self.assertEqual(data["exp"]-data["nbf"],120)


class VoiceHttpTests(unittest.TestCase):
    setUp=VoiceTests.setUp
    start=VoiceTests.start
    params=VoiceTests.params
    complete=VoiceTests.complete
    def test_http_auth_signatures_range_and_csrf(self):
        import app
        from auth_store import AuthStore
        from auth_http import LoginLimiter
        from twilio.request_validator import RequestValidator
        auth=AuthStore(Path(self.temp.name)/"auth.sqlite3");auth.initialize()
        auth.create_first_master("Equipe","master","Temporary-password-123!")
        token=auth.login("master","Temporary-password-123!")
        server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        server.auth_store=auth;server.login_limiter=LoginLimiter()
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None,headers=None,cookie=True):
            h={**(headers or {})}
            if cookie:h["Cookie"]="doc4docs_session="+token
            conn=http.client.HTTPConnection(*server.server_address,timeout=5)
            conn.request(method,path,body=body,headers=h);r=conn.getresponse();result=r.status,dict(r.getheaders()),r.read();conn.close();return result
        try:
            with patch("crm_api.store_for",return_value=self.store),patch("crm_voice.VoiceConfig.from_env",return_value=self.config),patch.object(app.Handler,"log_message"):
                key=self.complete();self.voice.archive_one()
                path=f"/api/crm/voice/audio?clinic=vielle&doctor={self.doc}&id={key}"
                self.assertEqual(request("GET",path,cookie=False)[0],401)
                self.assertEqual(request("GET",path.replace(self.doc,self.other))[0],404)
                status,headers,body=request("GET",path,headers={"Range":"bytes=0-2"})
                self.assertEqual((status,body),(206,b"ID3"));self.assertIn("no-store",headers["Cache-Control"])
                self.assertEqual(request("GET",path,headers={"Range":"bytes=999999-"})[0],416)
                self.assertEqual(request("POST",f"/api/crm/voice/note?clinic=vielle&doctor={self.doc}",json.dumps({"id":key,"doctor_id":self.doc}),headers={"Content-Type":"application/json"})[0],403)
                self.assertEqual(request("POST",f"/api/crm/voice/start-recording?clinic=vielle&doctor={self.doc}",json.dumps({"id":key,"doctor_id":self.doc,"verbal_authorization":True}),headers={"Content-Type":"application/json"})[0],403)
                self.assertEqual(request("POST",f"/api/crm/voice/start-recording?clinic=vielle&doctor={self.doc}",json.dumps({"id":key,"doctor_id":self.doc,"verbal_authorization":True}),headers={"Content-Type":"application/json"},cookie=False)[0],401)
                webhook=f"/api/crm-voice-webhook/status?id={key}"
                params=self.params(CallStatus="completed",CallDuration="65")
                headers={"Content-Type":"application/x-www-form-urlencoded"}
                self.assertEqual(request("POST",webhook,urlencode(params),headers,cookie=False)[0],403)
                headers["X-Twilio-Signature"]=RequestValidator(self.config.auth_token).compute_signature(self.config.origin+webhook,params)
                self.assertEqual(request("POST",webhook,urlencode(params),headers,cookie=False)[0],200)
                self.assertEqual(request("POST",webhook,urlencode({**params,"CallSid":"CA"+"a"*32}),headers,cookie=False)[0],403)
                self.assertEqual(request("POST",webhook,urlencode(params)+"&CallSid=duplicate",headers,cookie=False)[0],400)
        finally:
            server.shutdown();server.server_close();thread.join()
