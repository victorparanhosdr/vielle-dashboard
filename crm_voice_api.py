"""Authenticated call history/audio and signed Twilio callbacks. No public media URLs."""
import json
import re
from urllib.parse import parse_qs

from crm_voice import VoiceService, VoiceConfig
from task_api import read_body


def respond(handler, body, status=200, content_type="text/xml; charset=utf-8", extra=()):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    for name, value in extra:
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def handle_request(handler, store, action, query):
    voice = VoiceService(store)
    doctor = query.get("doctor", [""])[0]
    suffix = action.removeprefix("voice").strip("/")
    if handler.command == "GET":
        if suffix == "config":
            with store.db() as c:
                store.row(c, "doctors", doctor)
            return handler.auth_json({"ok": True, **voice.config.status(doctor)})
        if suffix == "history":
            return handler.auth_json({"ok": True, **voice.history(query.get("lead", [""])[0], doctor, query.get("page", [1])[0])})
        if suffix == "audio":
            key = query.get("id", [""])[0]
            path = voice.audio_path(key, doctor)
            # Range support keeps the native player seekable; reads remain session/doctor protected.
            size = path.stat().st_size
            start, end, status = 0, size-1, 200
            requested = handler.headers.get("Range", "")
            if requested:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
                if not match or int(match[1]) >= size or (match[2] and int(match[2]) < int(match[1])):
                    return respond(handler, b"", 416, "audio/mpeg", (("Content-Range", f"bytes */{size}"),))
                start, end, status = int(match[1]), min(int(match[2]) if match[2] else size-1, size-1), 206
            with path.open("rb") as audio:
                audio.seek(start)
                body = audio.read(end-start+1)
            handler.server.auth_store.audit_event("crm_voice_audio", handler.current_user["id"], details={"clinic": "vielle", "doctor_id": doctor, "id": key})
            extra = [("Accept-Ranges", "bytes"), ("Content-Disposition", 'inline; filename="ligacao.mp3"')]
            if status == 206:
                extra.append(("Content-Range", f"bytes {start}-{end}/{size}"))
            return respond(handler, body, status, "audio/mpeg", extra)
        raise LookupError()
    if handler.command != "POST":
        return handler.auth_json({"ok": False}, 405)
    if not handler.same_origin_request():
        return handler.auth_json({"ok": False}, 403)
    payload = json.loads(read_body(handler, 16*1024))
    if not isinstance(payload, dict) or payload.get("doctor_id") != doctor:
        raise ValueError("Doutor da solicitacao invalido.")
    actor = handler.current_user
    if suffix == "prepare":
        result = voice.prepare(payload, actor)
    elif suffix == "start-recording":
        result = voice.start_recording(payload, actor)
    elif suffix == "note":
        voice.save_note(payload, actor)
        result = {}
    elif suffix == "cancel":
        voice.cancel(payload, actor)
        result = {}
    elif suffix == "retry-recording":
        voice.retry_recording(payload)
        result = {}
    else:
        raise LookupError()
    handler.server.auth_store.audit_event("crm_" + action, actor["id"], details={"clinic": "vielle", "doctor_id": doctor, "id": payload.get("id", result.get("call", {}).get("id", ""))})
    return handler.auth_json({"ok": True, **result})


def webhook(handler, parsed):
    from crm_api import store_for
    from twilio.request_validator import RequestValidator
    config = VoiceConfig.from_env()
    action = parsed.path.removeprefix("/api/crm-voice-webhook/")
    query = parse_qs(parsed.query, keep_blank_values=True)
    valid_query = (not parsed.query) if action == "twiml" else (set(query) == {"id"} and len(query["id"]) == 1 and bool(re.fullmatch(r"[a-f0-9]{32}", query["id"][0])))
    if action not in {"twiml", "notice", "consent", "status", "finished", "recording"} or not valid_query:
        return respond(handler, b"", 404)
    if not config.credentials_ready or not config.origin_ready:
        return respond(handler, b"", 503)
    try:
        if handler.headers.get_content_type() != "application/x-www-form-urlencoded":
            raise ValueError()
        raw = parse_qs(read_body(handler, 32*1024).decode("utf-8"), keep_blank_values=True, max_num_fields=100)
        if any(len(v) != 1 for v in raw.values()):
            raise ValueError()
        params = {k: v[0] for k, v in raw.items()}
        expected_url = config.origin + parsed.path + ("?" + parsed.query if parsed.query else "")
        if params.get("AccountSid") != config.account or not RequestValidator(config.auth_token).validate(expected_url, params, handler.headers.get("X-Twilio-Signature", "")):
            return respond(handler, b"", 403)
        key = params.get("CallId", "") if action == "twiml" else query["id"][0]
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise ValueError()
        result = VoiceService(store_for(handler.server.auth_store)).callback(action, key, params)
        return respond(handler, result.encode("utf-8"))
    except (ValueError, LookupError, TypeError, UnicodeError):
        return respond(handler, b"", 400)
    except Exception:
        # Neither provider payloads nor signed URLs/audio/credentials belong in logs.
        return respond(handler, b"", 503)
