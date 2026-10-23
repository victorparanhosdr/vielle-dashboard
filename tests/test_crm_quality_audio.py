import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error
import uuid
import wave
from unittest.mock import Mock,patch

import test_crm_quality as base
import crm_media
import crm_quality as quality
import crm_quality_audio as audio
import crm_quality_provider as provider


def wav():
    result=io.BytesIO()
    with wave.open(result,"wb") as file:
        file.setparams((1,2,8000,0,"NONE","not compressed"));file.writeframes(b"\0\0"*8000)
    return result.getvalue()


class QualityAudioTests(base.QualityTests):
    def setUp(self):
        super().setUp()
        patcher=patch("crm_quality_audio.audio_model",return_value="synthetic-transcriber")
        patcher.start();self.addCleanup(patcher.stop)

    def attach_audio(self,direction="in",actor=""):
        ident=uuid.uuid4().hex
        media=crm_media.upload(self.store,self.doc,self.channel,self.contact,"master-1",uuid.uuid4().hex,"audio.wav","audio",wav())
        self.message(ident,"[Audio]",direction,actor=actor,name="Equipe Exemplo" if actor else "",media=media["id"],stamp=self.now-10)
        with self.store.db() as c:c.execute("UPDATE message_media SET message_id=? WHERE id=?",(ident,media["id"]))
        return ident,media

    def queue_audio(self):
        self.authorize()
        return quality.request(self.store,self.doc,self.channel,self.contact,"master-1",include_audio=True)

    def execute(self,ident,transcriber=None,model=None):
        transcriber=transcriber or Mock(return_value="Gostaria de agendar uma avaliação para a próxima semana.")
        model=model or Mock(side_effect=self.raw)
        quality.tick(self.store,model,lambda _:True,transcriber=transcriber)
        return quality.detail(self.store,self.doc,ident),transcriber,model

    def test_audio_is_not_sent_without_per_request_selection(self):
        self.attach_audio();self.authorize();ident=self.request()
        job,transcriber,_=self.execute(ident)
        transcriber.assert_not_called();self.assertTrue(job["partial"])

    def test_transcription_preserves_direction_authorship_and_private_evidence(self):
        message,_=self.attach_audio("out","11");ident=self.queue_audio()
        job,transcriber,model=self.execute(ident)
        self.assertEqual(job["status"],"done");self.assertFalse(job["partial"])
        original=next(m for m in job["messages"] if m["message_id"]==message)
        self.assertEqual((original["kind"],original["actor"]),("human","a1"))
        self.assertEqual(original["transcription"]["source"],"automatic_audio")
        sent=json.loads(model.call_args.args[0][1]["content"])
        self.assertTrue(any("próxima semana" in m["text"] for m in sent["messages"]))
        self.assertTrue(all("media_id" not in m and "media_sha256" not in m and "message_id" not in m for m in sent["messages"]))
        self.assertEqual(job["audio_summary"]["transcribed"],1);transcriber.assert_called_once()

    def test_successful_transcription_is_reused_without_new_audio_call(self):
        self.attach_audio();ident=self.queue_audio();job,transcriber,_=self.execute(ident)
        self.message("new-context","Tenho mais uma dúvida.","in",stamp=self.now-2)
        other=quality.request(self.store,self.doc,self.channel,self.contact,"master-1",include_audio=True)
        job,_,_=self.execute(other,transcriber)
        self.assertEqual(job["status"],"done");self.assertEqual(job["audio_summary"]["reused"],1)
        transcriber.assert_called_once()

    def test_audio_failure_has_specific_diagnostic_and_no_score_or_auto_retry(self):
        self.attach_audio();ident=self.queue_audio()
        job,transcriber,model=self.execute(ident,Mock(side_effect=provider.QualityError("api_quota")))
        self.assertEqual((job["status"],job["error_code"]),("failed","api_quota"));self.assertEqual(job["assessments"],[])
        model.assert_not_called();self.assertFalse(quality.tick(self.store,model,lambda _:True,transcriber=transcriber))
        retry=quality.request(self.store,self.doc,self.channel,self.contact,"master-1",include_audio=True,retry_from=ident,request_id="synthetic-retry-123456")
        job,_,_=self.execute(retry)
        self.assertEqual(job["status"],"done")

    def test_failed_cache_is_not_retried_by_a_different_context(self):
        self.attach_audio();ident=self.queue_audio();self.execute(ident,Mock(side_effect=provider.QualityError("api_timeout")))
        self.message("new-context","Outro contexto.","in",stamp=self.now-2)
        other=quality.request(self.store,self.doc,self.channel,self.contact,"master-1",include_audio=True)
        job,transcriber,model=self.execute(other)
        self.assertEqual(job["error_code"],"audio_previous");transcriber.assert_not_called();model.assert_not_called()

    def test_failed_concurrent_claim_is_not_retried_after_decoding(self):
        _,media=self.attach_audio();ident=self.queue_audio()
        original=audio.wav_bytes
        def concurrent(data):
            result=original(data)
            with self.store.db() as c:
                row=c.execute("SELECT sha256 FROM message_media WHERE id=?",(media["id"],)).fetchone()
                c.execute("INSERT INTO crm_audio_transcripts(media_id,sha256,model,status,job_id,created_at,updated_at) VALUES(?,?,?,'failed',?,?,?)",
                    (media["id"],row["sha256"],"synthetic-transcriber","other-job",self.now,self.now))
            return result
        with patch("crm_quality_audio.wav_bytes",side_effect=concurrent):job,transcriber,model=self.execute(ident)
        self.assertEqual(job["error_code"],"audio_previous");transcriber.assert_not_called();model.assert_not_called()

    def test_audio_orthography_does_not_generate_deduction(self):
        message,_=self.attach_audio("out","11");ident=self.queue_audio()
        def result(messages):
            raw=self.raw(messages,["professionalism"])
            data=json.loads(messages[1]["content"])
            ref=next(m["id"] for m in data["messages"] if m.get("transcription"))
            next(i for i in raw["assessments"][0]["items"] if i["id"]=="professionalism")["evidence"]=[ref]
            return raw
        job,_,_=self.execute(ident,model=Mock(side_effect=result))
        item=next(i for i in job["assessments"][0]["result"]["items"] if i["id"]=="professionalism")
        self.assertEqual(item["outcome"],"na")

    def test_scope_revoked_during_transcription_discards_result_and_stops_model(self):
        self.attach_audio();ident=self.queue_audio()
        def revoked(*args,**kwargs):
            with self.store.db() as c:c.execute("UPDATE quality_settings SET revision=revision+1")
            return "Fala sintética"
        job,_,model=self.execute(ident,Mock(side_effect=revoked))
        self.assertEqual((job["status"],job["error_code"]),("cancelled","scope_changed"));model.assert_not_called()
        with self.store.db() as c:self.assertEqual(c.execute("SELECT text FROM crm_audio_transcripts").fetchone()[0],"")

    def test_missing_and_modified_files_never_reach_provider(self):
        _,media=self.attach_audio();ident=self.queue_audio()
        crm_media.path_for(self.store,media["id"]).unlink()
        job,transcriber,model=self.execute(ident)
        self.assertEqual(job["error_code"],"audio_missing");transcriber.assert_not_called();model.assert_not_called()

    def test_audio_count_limit_fails_before_any_paid_request(self):
        for _ in range(9):self.attach_audio()
        ident=self.queue_audio();job,transcriber,model=self.execute(ident)
        self.assertEqual(job["error_code"],"audio_limit");transcriber.assert_not_called();model.assert_not_called()

    def test_unattributed_audio_never_infers_a_speaker(self):
        message,_=self.attach_audio("out","");ident=self.queue_audio()
        job,_,_=self.execute(ident)
        row=next(m for m in job["messages"] if m["message_id"]==message)
        self.assertEqual((row["kind"],row["actor"]),("unattributed",""));self.assertTrue(job["partial"])

    def test_text_failure_can_be_retried_with_explicit_audio_selection(self):
        self.attach_audio();self.authorize();ident=self.request()
        quality.tick(self.store,Mock(side_effect=RuntimeError()),lambda _:True)
        retry=quality.request(self.store,self.doc,self.channel,self.contact,"master-1",include_audio=True,retry_from=ident,request_id="synthetic-retry-audio-123")
        job,_,_=self.execute(retry);self.assertEqual(job["status"],"done")

    def test_ffmpeg_conversion_and_ogg_are_bounded_and_preserve_original(self):
        from imageio_ffmpeg import get_ffmpeg_exe
        import subprocess
        raw=wav()
        ogg=subprocess.run([get_ffmpeg_exe(),"-v","error","-f","wav","-i","pipe:0","-c:a","libopus","-f","ogg","pipe:1"],input=raw,capture_output=True,check=True).stdout
        converted=audio.wav_bytes(ogg)
        with wave.open(io.BytesIO(converted),"rb") as decoded:
            self.assertEqual(decoded.getnchannels(),1);self.assertEqual(decoded.getframerate(),16000)
            self.assertGreater(decoded.getnframes(),100)
        with self.assertRaises(provider.QualityError):audio.wav_bytes(b"not an audio")


class ProviderDiagnosticTests(base.unittest.TestCase):
    def test_quality_provider_requests_strict_schema_without_affecting_legacy_defaults(self):
        call=Mock(return_value={"assessments":[]})
        fake=SimpleNamespace(openai_chat_json=call)
        messages=[{"role":"system","content":"Dados sintéticos"},{"role":"user","content":json.dumps({"actors":["a1"],"rubric":[{"id":"clarity"}],"messages":[{"id":"m1"}]})}]
        with patch.dict("sys.modules",{"app":fake}):provider.chat(messages)
        schema=call.call_args.kwargs["schema"]
        self.assertFalse(schema["additionalProperties"]);self.assertEqual(schema["required"],["assessments"])

    def test_http_diagnostics_are_distinct_and_do_not_store_raw_content(self):
        cases=[(401,{},"api_key"),(429,{"code":"insufficient_quota"},"api_quota"),(429,{},"api_rate"),(404,{},"api_model"),(400,{},"api_request"),(503,{},"api_unavailable")]
        for status,error,expected in cases:
            failure=provider.http_error(status,json.dumps({"error":error | {"message":"private body"}}))
            self.assertEqual(failure.code,expected);self.assertNotIn("private",str(failure))

    def test_multipart_request_is_private_bounded_and_uses_frozen_model(self):
        fake=SimpleNamespace(config_value=lambda key,default=None:"synthetic-key" if key=="OPENAI_API_KEY" else default,configured=bool)
        response=io.BytesIO(b'{"text":"Fala sintetica"}')
        with patch.dict("sys.modules",{"app":fake}),patch("urllib.request.urlopen",return_value=response) as call:
            self.assertEqual(provider.transcribe(wav(),model="synthetic-transcriber"),"Fala sintetica")
        request=call.call_args.args[0]
        self.assertEqual(request.full_url,"https://api.openai.com/v1/audio/transcriptions")
        self.assertIn(b"synthetic-transcriber",request.data);self.assertIn(b'filename="audio.wav"',request.data)

    def test_quota_error_and_timeout_do_not_retry(self):
        fake=SimpleNamespace(config_value=lambda *args:"synthetic-key",configured=bool)
        error=urllib.error.HTTPError("https://api.openai.com",429,"failure",{},io.BytesIO(b'{"error":{"code":"insufficient_quota","message":"private"}}'))
        with patch.dict("sys.modules",{"app":fake}),patch("urllib.request.urlopen",side_effect=error) as call:
            with self.assertRaises(provider.QualityError) as raised:provider.transcribe(wav(),model="synthetic-transcriber")
        self.assertEqual(raised.exception.code,"api_quota");call.assert_called_once()
