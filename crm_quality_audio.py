"""Authorized audio preparation for a single quality review; no live-audio crawler."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import wave

from crm_quality_provider import QualityError,classify,audio_model

MAX_AUDIO=8
VERSION="audio-native-v1"


def schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS crm_audio_transcripts(
        media_id TEXT NOT NULL,sha256 TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',error_code TEXT NOT NULL DEFAULT '',job_id TEXT NOT NULL,
        created_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(media_id,sha256,model))""")


def wav_bytes(data):
    from imageio_ffmpeg import get_ffmpeg_exe
    try:
        with tempfile.TemporaryDirectory(prefix="doc4docs-audio-") as directory:
            output=Path(directory)/"audio.wav"
            result=subprocess.run([get_ffmpeg_exe(),"-nostdin","-hide_banner","-loglevel","error","-max_alloc","67108864","-threads","1",
                "-protocol_whitelist","pipe","-i","pipe:0","-t","601","-fs","20000000","-map_metadata","-1","-vn","-ac","1","-ar","16000",str(output)],
                input=data,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=40,check=False)
            if result.returncode or not output.is_file() or output.stat().st_size>20000000:raise QualityError("audio_decode")
            with wave.open(str(output),"rb") as audio:
                seconds=audio.getnframes()/audio.getframerate()
            if seconds>600:raise QualityError("audio_limit")
            if seconds<.1:raise QualityError("audio_empty")
            return output.read_bytes()
    except QualityError:raise
    except Exception:raise QualityError("audio_decode") from None


def load(store,job,message,gateway):
    import crm_media
    with store.db() as c:
        row=c.execute("SELECT * FROM message_media WHERE id=? AND message_id=?",(message["media_id"],message["message_id"])).fetchone()
        if not row or (row["doctor_id"],row["source_channel_id"],row["contact_id"],row["kind"])!=(job["doctor_id"],job["channel_id"],job["contact_id"],"audio"):
            raise QualityError("audio_changed")
        if message.get("media_sha256") and message["media_sha256"]!=row["sha256"]:raise QualityError("audio_changed")
        row=dict(row)
    if row["download_state"]!="ready":
        if gateway is None:raise QualityError("audio_missing")
        try:crm_media.fetch_media(store,gateway,job["doctor_id"],row["id"])
        except Exception:raise QualityError("audio_missing") from None
        with store.db() as c:row=store.row(c,"message_media",row["id"])
    path=crm_media.path_for(store,row.get("followup_asset_id") or row["id"])
    if path.is_symlink() or not path.is_file() or row["size"]>crm_media.LIMITS["audio"]:raise QualityError("audio_missing")
    with path.open("rb") as source:data=source.read(crm_media.LIMITS["audio"]+1)
    if len(data)!=row["size"] or hashlib.sha256(data).hexdigest()!=row["sha256"]:raise QualityError("audio_changed")
    return row,data


def transcript(store,job,message,provider,gateway,allowed,*,before_call=None):
    if not allowed():raise QualityError("scope_changed")
    row,raw=load(store,job,message,gateway);model=job["_audio_model"];key=(row["id"],row["sha256"],model)
    now=time.time()
    with store.db() as c:
        cached=c.execute("SELECT * FROM crm_audio_transcripts WHERE media_id=? AND sha256=? AND model=?",key).fetchone()
        if cached and cached["status"]=="done":return cached["text"],True
        if cached and cached["status"]=="running" and cached["updated_at"]>now-900:raise QualityError("audio_pending")
        if cached and not job["retry_from"]:raise QualityError("audio_previous")
    wav=wav_bytes(raw)
    if not allowed():raise QualityError("scope_changed")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        cached=c.execute("SELECT * FROM crm_audio_transcripts WHERE media_id=? AND sha256=? AND model=?",key).fetchone()
        if cached and cached["status"]=="done":return cached["text"],True
        if cached and cached["status"]=="running" and cached["updated_at"]>time.time()-900:raise QualityError("audio_pending")
        if cached and not job["retry_from"]:raise QualityError("audio_previous")
        if before_call:before_call(c,wav)
        c.execute("""INSERT INTO crm_audio_transcripts(media_id,sha256,model,status,job_id,created_at,updated_at) VALUES(?,?,?,'running',?,?,?)
            ON CONFLICT(media_id,sha256,model) DO UPDATE SET status='running',text='',error_code='',job_id=excluded.job_id,updated_at=excluded.updated_at""",(*key,job["id"],now,now))
    try:
        text=provider(wav,model=model)
        if not isinstance(text,str) or not text.strip() or len(text)>30000:raise QualityError("audio_empty")
        if not allowed():raise QualityError("scope_changed")
    except Exception as exc:
        failure=classify(exc)
        with store.db() as c:c.execute("UPDATE crm_audio_transcripts SET status='failed',error_code=?,updated_at=? WHERE media_id=? AND sha256=? AND model=? AND job_id=?",(failure.code,time.time(),*key,job["id"]))
        raise failure from None
    with store.db() as c:c.execute("UPDATE crm_audio_transcripts SET status='done',text=?,error_code='',updated_at=? WHERE media_id=? AND sha256=? AND model=? AND job_id=?",(text.strip(),time.time(),*key,job["id"]))
    return text.strip(),False


def prepare(store,job,data,provider,gateway,allowed):
    from crm_quality import redact
    if not job["include_audio"]:return data
    job["_audio_model"]=audio_model()
    pending=[m for m in data["messages"] if m.get("media_kind")=="audio"]
    if len(pending)>MAX_AUDIO:raise QualityError("audio_limit")
    if pending and not callable(provider):raise QualityError("audio_missing")
    for message in pending:
        text,reused=transcript(store,job,message,provider,gateway,allowed)
        text=redact(text)
        if len(text)>8000:text=text[:8000]+" [transcrição incompleta]";data["base_partial"]=True
        caption=message["text"] if message["text"] not in {"[Audio]","[Áudio]","[audio]","[áudio]",""} else ""
        message.update(text=(caption+"\n" if caption else "")+text,media_unavailable=False,
            transcription={"source":"automatic_audio","reused":reused,"model":job["_audio_model"],"version":VERSION})
        done=[m for m in pending if m.get("transcription")]
        data["audio_summary"]={"requested":True,"total":len(pending),"transcribed":len(done),"reused":sum(bool(m["transcription"]["reused"]) for m in done)}
        with store.db() as c:c.execute("UPDATE quality_jobs SET snapshot=? WHERE id=? AND status='running'",(json.dumps(data),job["id"]))
    total=0
    for message in data["messages"]:
        remaining=max(0,45000-total)
        if len(message["text"])>remaining:
            message["text"]=message["text"][:remaining];data["base_partial"]=True
        total+=len(message["text"])
    data["partial"]=bool(data.get("base_partial") or any(m["media_unavailable"] or m["kind"]=="unattributed" for m in data["messages"]))
    data["audio_summary"]={"requested":True,"transcribed":len(pending),"reused":sum(m.get("transcription",{}).get("reused",False) for m in pending)}
    return data
