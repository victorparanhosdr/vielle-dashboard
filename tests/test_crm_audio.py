import io
import json
import time
import unittest
import wave
from unittest.mock import Mock,patch
from urllib.parse import urlparse

import crm_audio as audio
import crm_media
import crm_quality as quality
from crm_quality_provider import QualityError
from crm_access import crm_access
import test_crm_quality as base
import test_crm_quality_audio as audio_base


class AttendanceAudioTests(unittest.TestCase):
    setUp=base.QualityTests.setUp
    message=base.QualityTests.message
    attach_audio=audio_base.QualityAudioTests.attach_audio
    authorize=base.QualityTests.authorize
    raw=base.QualityTests.raw

    def enable(self,**kw):
        with self.store.db() as c:s=audio.settings(c,self.doc)
        return audio.save(self.store,self.doc,{"enabled":True,"daily_minutes":60,"revision":s["revision"],"confirm":True,**kw},"master-1")

    def queue(self,*,live=True):
        ident,media=self.attach_audio()
        with self.store.db() as c:job=audio.enqueue(c,self.store,ident,time.time()) if live else None
        return job,ident,media

    def run_audio(self,provider=None,permitted=None):
        provider=provider or Mock(return_value="Fala sintética de exemplo.")
        with patch("crm_audio.audio_model",return_value="synthetic-transcriber"):
            audio.tick(self.store,provider,permitted or (lambda _:True))
        return provider

    def job(self,ident):
        with self.store.db() as c:return dict(c.execute("SELECT * FROM crm_audio_jobs WHERE id=?",(ident,)).fetchone())

    def test_off_by_default_and_reads_never_enqueue_or_call_provider(self):
        job,_,_=self.queue();self.assertIsNone(job)
        audio.overview(self.store,self.doc);self.store.conversation(self.doc,self.channel,self.contact)
        provider=Mock();self.assertFalse(audio.tick(self.store,provider,lambda _:True));provider.assert_not_called()

    def test_enable_never_enrolls_existing_audio_and_requires_revision_consent(self):
        self.queue();self.enable()
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM crm_audio_jobs").fetchone()[0],0)
        for changes in ({"confirm":False},{"daily_minutes":0},{"daily_minutes":1441},{"revision":0},{"enabled":"yes"}):
            with self.assertRaises(ValueError):self.enable(**changes)

    def test_new_audio_is_transcribed_and_exposed_only_in_its_conversation(self):
        self.enable();job,_,_=self.queue();provider=self.run_audio()
        self.assertEqual(self.job(job)["status"],"done");provider.assert_called_once()
        thread=self.store.conversation(self.doc,self.channel,self.contact)
        transcript=next(m["media"]["transcript"] for m in thread["messages"] if m.get("media"))
        self.assertEqual(transcript["text"],"Fala sintética de exemplo.")
        self.assertNotIn("assessments",transcript);self.assertEqual(audio.overview(self.store,self.doc)["used_seconds"],1)

    def test_quality_reuses_attendance_cache_without_another_audio_call(self):
        self.enable();self.queue();self.run_audio();self.authorize()
        ident=quality.request(self.store,self.doc,self.channel,self.contact,"master-1",include_audio=True)
        provider=Mock()
        with patch("crm_quality_audio.audio_model",return_value="synthetic-transcriber"):
            quality.tick(self.store,Mock(side_effect=self.raw),lambda _:True,transcriber=provider)
        provider.assert_not_called();self.assertEqual(quality.detail(self.store,self.doc,ident)["audio_summary"]["reused"],1)

    def test_attendance_reuses_quality_cache_without_reserving_minutes(self):
        self.enable();_,ident,_=self.queue(live=False);self.authorize()
        quality.request(self.store,self.doc,self.channel,self.contact,"master-1",include_audio=True)
        with patch("crm_quality_audio.audio_model",return_value="synthetic-transcriber"):
            quality.tick(self.store,Mock(side_effect=self.raw),lambda _:True,transcriber=Mock(return_value="Fala de exemplo"))
        with self.store.db() as c:job=audio.enqueue(c,self.store,ident,time.time())
        provider=Mock();self.run_audio(provider);provider.assert_not_called()
        self.assertEqual(self.job(job)["status"],"done");self.assertEqual(audio.overview(self.store,self.doc)["used_seconds"],0)

    def test_parallel_workers_cannot_exceed_daily_reservation(self):
        from concurrent.futures import ThreadPoolExecutor
        self.enable(daily_minutes=1);first,_,_=self.queue();second,_,_=self.queue()
        with self.store.db() as c:c.execute("INSERT INTO crm_audio_usage VALUES('prior-call',?,?,59)",(self.doc,audio.day(time.time())))
        provider=Mock(return_value="Fala sintética")
        with patch("crm_audio.audio_model",return_value="synthetic-transcriber"),ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(audio.tick,self.store,provider,lambda _:True) for _ in range(2)]
            for future in futures:self.assertTrue(future.result())
        provider.assert_called_once();self.assertEqual(audio.overview(self.store,self.doc)["used_seconds"],60)
        self.assertEqual({self.job(first)["status"],self.job(second)["status"]},{"done","limited"})

    def test_daily_quota_hold_rolls_over_without_repeating_a_failed_call(self):
        self.enable(daily_minutes=1);job,_,_=self.queue()
        with self.store.db() as c:
            c.execute("UPDATE crm_audio_jobs SET status='limited',quota_day='2000-01-01' WHERE id=?",(job,))
            c.execute("INSERT INTO crm_audio_usage VALUES('old-day',?,'2000-01-01',60)",(self.doc,))
        provider=self.run_audio();provider.assert_called_once();self.assertEqual(self.job(job)["status"],"done")

    def test_file_larger_than_daily_ceiling_requires_human_review_not_daily_loop(self):
        self.enable(daily_minutes=1);job,_,_=self.queue();data=io.BytesIO()
        with wave.open(data,"wb") as file:file.setparams((1,2,16000,0,"NONE","not compressed"));file.writeframes(b"\0\0"*(16000*61))
        provider=Mock()
        with patch("crm_quality_audio.wav_bytes",return_value=data.getvalue()):self.run_audio(provider)
        provider.assert_not_called();self.assertEqual(self.job(job)["error_code"],"audio_budget_size")
        self.assertEqual(self.job(job)["status"],"failed")

    def test_budget_is_reserved_before_call_and_holds_without_paid_retry(self):
        self.enable(daily_minutes=1);job,_,_=self.queue();data=io.BytesIO()
        with wave.open(data,"wb") as file:file.setparams((1,2,16000,0,"NONE","not compressed"));file.writeframes(b"\0\0"*(16000*2))
        with self.store.db() as c:c.execute("INSERT INTO crm_audio_usage VALUES('prior-call',?,?,59)",(self.doc,audio.day(time.time())))
        provider=Mock()
        with patch("crm_quality_audio.wav_bytes",return_value=data.getvalue()):self.run_audio(provider)
        provider.assert_not_called();self.assertEqual(self.job(job)["status"],"limited")
        self.assertEqual(audio.overview(self.store,self.doc)["used_seconds"],59)
        provider.assert_not_called();self.run_audio(provider);provider.assert_not_called()

    def test_timeout_keeps_reservation_and_does_not_repeat(self):
        self.enable();job,_,_=self.queue();provider=Mock(side_effect=TimeoutError())
        self.run_audio(provider);self.assertEqual(self.job(job)["status"],"failed")
        self.assertEqual(audio.overview(self.store,self.doc)["used_seconds"],1)
        self.run_audio(provider);provider.assert_called_once()
        audio.retry(self.store,self.doc,job,"master-1")
        self.run_audio();self.assertEqual(self.job(job)["status"],"done")
        self.assertEqual(audio.overview(self.store,self.doc)["used_seconds"],2)

    def test_disable_cancels_queue_and_inflight_result_is_discarded(self):
        self.enable();job,_,_=self.queue()
        def revoke(*a,**kw):
            self.enable(enabled=False,confirm=False)
            return "Fala descartada"
        self.run_audio(Mock(side_effect=revoke));self.assertEqual(self.job(job)["status"],"cancelled")
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM crm_audio_transcripts WHERE text<>''").fetchone()[0],0)

    def test_revoked_authorizer_does_not_call_provider(self):
        self.enable();job,_,_=self.queue();provider=Mock();self.run_audio(provider,lambda _:False)
        provider.assert_not_called();self.assertEqual(self.job(job)["status"],"cancelled")

    def test_revoked_batch_or_retry_authorizer_stops_even_when_policy_owner_is_active(self):
        self.enable();job,_,_=self.queue()
        with self.store.db() as c:c.execute("UPDATE crm_audio_jobs SET created_by='former-master' WHERE id=?",(job,))
        provider=Mock();self.run_audio(provider,lambda ident:ident=="master-1")
        provider.assert_not_called();self.assertEqual(self.job(job)["status"],"cancelled")

    def test_channel_transfer_or_media_scope_change_cancels_job(self):
        self.enable();job,_,media=self.queue()
        with self.store.db() as c:c.execute("UPDATE message_media SET doctor_id=? WHERE id=?",(self.other,media["id"]))
        provider=Mock();self.run_audio(provider);provider.assert_not_called()
        self.assertEqual(self.job(job)["status"],"cancelled")

    def test_preview_is_read_only_and_apply_is_frozen_idempotent(self):
        self.queue(live=False);self.enable()
        p={"from":audio.day(self.now),"to":audio.day(self.now),"limit":1}
        batch=audio.preview_batch(self.store,self.doc,p,"master-1")
        self.assertEqual(batch["count"],1)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM crm_audio_jobs").fetchone()[0],0)
        with self.assertRaises(ValueError):audio.apply_batch(self.store,self.other,batch["id"],"master-1")
        result=audio.apply_batch(self.store,self.doc,batch["id"],"master-1");self.assertEqual(result["queued"],1)
        self.assertTrue(audio.apply_batch(self.store,self.doc,batch["id"],"master-1")["already_applied"])

    def test_changed_settings_invalidates_preview(self):
        self.queue(live=False);self.enable()
        batch=audio.preview_batch(self.store,self.doc,{"from":audio.day(self.now),"to":audio.day(self.now)},"master-1")
        self.enable(daily_minutes=30)
        with self.assertRaises(ValueError):audio.apply_batch(self.store,self.doc,batch["id"],"master-1")

    def test_historical_sync_does_not_enroll_but_live_webhook_does(self):
        self.enable()
        with self.store.db() as c:instance=c.execute("SELECT instance FROM channels WHERE id=?",(self.channel,)).fetchone()[0]
        payload={"key":{"id":"synthetic-audio","remoteJid":"5511900000009@s.whatsapp.net","fromMe":True},"messageTimestamp":int(time.time()),"message":{"audioMessage":{"mimetype":"audio/ogg","seconds":1}}}
        self.store.receive(instance,payload,"messages.upsert",history=True)
        self.assertEqual(audio.overview(self.store,self.doc)["counts"],{})
        payload["key"]["id"]="synthetic-live-audio";payload["key"]["fromMe"]=False
        self.store.receive(instance,payload,"messages.upsert")
        self.assertEqual(audio.overview(self.store,self.doc)["counts"].get("queued"),1)
        self.store.receive(instance,payload,"messages.upsert")
        self.assertEqual(audio.overview(self.store,self.doc)["counts"].get("queued"),1)

    def test_doctor_and_funnel_limits_apply_to_transcript_reads(self):
        self.enable();self.queue();self.run_audio()
        with crm_access({self.doc:[]}):
            with self.assertRaises(PermissionError):self.store.conversation(self.doc,self.channel,self.contact)
        with crm_access({self.doc:None}):
            self.assertTrue(any(m.get("media",{}).get("transcript",{}).get("text") for m in self.store.conversation(self.doc,self.channel,self.contact)["messages"]))
            with self.assertRaises((ValueError,PermissionError)):self.store.conversation(self.other,self.channel,self.contact)

    def test_local_day_uses_sao_paulo_not_utc(self):
        from datetime import datetime,timezone
        stamp=datetime(2026,10,23,1,30,tzinfo=timezone.utc).timestamp()
        self.assertEqual(audio.day(stamp),"2026-10-22")
