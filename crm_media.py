"""Private WhatsApp attachments; never expose provider or public storage URLs."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
import threading
from urllib.parse import quote, unquote, urlencode
import uuid

from crm_store import Conflict, text
from task_api import read_body

MB = 1024 * 1024
LIMITS = {"image": 10 * MB, "audio": 16 * MB, "video": 16 * MB, "document": 25 * MB}
TYPES = {
    ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"), ".png": ("image", "image/png"),
    ".webp": ("image", "image/webp"), ".gif": ("image", "image/gif"),
    ".mp3": ("audio", "audio/mpeg"), ".ogg": ("audio", "audio/ogg"), ".opus": ("audio", "audio/ogg"),
    ".wav": ("audio", "audio/wav"), ".m4a": ("audio", "audio/mp4"), ".aac": ("audio", "audio/aac"),
    ".webm": ("audio", "audio/webm"), ".mp4": ("video", "video/mp4"), ".mov": ("video", "video/quicktime"),
    ".pdf": ("document", "application/pdf"), ".txt": ("document", "text/plain"), ".csv": ("document", "text/csv"),
    ".doc": ("document", "application/msword"), ".xls": ("document", "application/vnd.ms-excel"),
    ".ppt": ("document", "application/vnd.ms-powerpoint"), ".zip": ("document", "application/zip"),
    ".docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xlsx": ("document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".pptx": ("document", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
}
LABELS = {"image": "Imagem", "audio": "Audio", "video": "Video", "document": "Arquivo"}
FETCH_GATE, FETCH_LOCK, FETCHING = threading.BoundedSemaphore(2), threading.Lock(), set()


def validate(name, kind, data):
    name = text(name, 180)
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or "/" in name or "\\" in name:
        raise ValueError("Nome de arquivo invalido.")
    ext = Path(name).suffix.lower()
    expected, mime = TYPES.get(ext, ("", ""))
    if kind == "audio" and ext == ".mp4":
        expected, mime = "audio", "audio/mp4"
    if kind not in LIMITS or expected != kind:
        raise ValueError("Formato nao aceito para este tipo de anexo.")
    if not data or len(data) > LIMITS[kind]:
        raise ValueError(f"O limite deste anexo e {LIMITS[kind] // MB} MB.")
    header = data[:64]
    signatures = {
        ".jpg": header.startswith(b"\xff\xd8\xff"), ".jpeg": header.startswith(b"\xff\xd8\xff"),
        ".png": header.startswith(b"\x89PNG\r\n\x1a\n"), ".webp": header[:4] == b"RIFF" and header[8:12] == b"WEBP",
        ".gif": header[:6] in (b"GIF87a", b"GIF89a"), ".pdf": header.startswith(b"%PDF-"),
        ".ogg": header.startswith(b"OggS"), ".opus": header.startswith(b"OggS"),
        ".wav": header[:4] == b"RIFF" and header[8:12] == b"WAVE",
        ".webm": header.startswith(b"\x1a\x45\xdf\xa3"),
        ".mp3": header.startswith(b"ID3") or len(header) > 1 and header[0] == 255 and header[1] & 224 == 224,
        ".aac": len(header) > 1 and header[0] == 255 and header[1] & 246 == 240,
    }
    if ext in {".mp4", ".mov", ".m4a"}:
        valid = header[4:8] == b"ftyp"
    elif ext in {".zip", ".docx", ".xlsx", ".pptx"}:
        valid = header.startswith(b"PK\x03\x04")
    elif ext in {".doc", ".xls", ".ppt"}:
        valid = header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    elif ext in {".txt", ".csv"}:
        try:
            valid = b"\0" not in data and bool(data.decode("utf-8-sig").strip())
        except UnicodeError:
            valid = False
    else:
        valid = signatures.get(ext, False)
    if not valid:
        raise ValueError("O conteudo do arquivo nao confere com o formato informado.")
    return name, mime


def storage_used(c):
    return c.execute("""SELECT
        (SELECT coalesce(sum(size),0) FROM message_media WHERE download_state='ready' AND followup_asset_id='')+
        (SELECT coalesce(sum(size),0) FROM crm_followup_assets)""").fetchone()[0]


def public(row):
    return {k: row[k] for k in ("id", "name", "mime", "kind", "size")} | {"state": row.get("download_state", "ready"), "url": "/api/crm/media?" + urlencode({"clinic": "vielle", "doctor": row["doctor_id"], "id": row["id"]})} | ({"is_sticker": True} if row.get("is_sticker") else {})


def path_for(store, key):
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("Anexo invalido.")
    return store.path.parent / "media" / key


def upload(store, doctor, channel, contact, user, request, name, kind, data):
    name, mime = validate(name, kind, data)
    request = text(request, 100)
    digest = hashlib.sha256(data).hexdigest()
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        ch, ct = store.conversation_contact(c, doctor, channel, contact)
        from crm_channels import require_active
        require_active(ch)
        if ct["opted_out"]:
            raise ValueError("Contato descadastrado. Envio bloqueado.")
        old = c.execute("SELECT * FROM message_media WHERE user_id=? AND upload_request=?", (str(user), request)).fetchone()
        if old:
            if (old["doctor_id"], old["source_channel_id"], old["contact_id"], old["name"], old["kind"], old["sha256"]) != (doctor, channel, contact, name, kind, digest):
                raise Conflict("Identificador de anexo ja utilizado.")
            return public(dict(old))
        # Unsent drafts expire; files already linked to conversations are retained.
        stale = c.execute("SELECT id FROM message_media WHERE message_id='' AND created_at<?", (time.time() - 86400,)).fetchall()
        for row in stale:
            path_for(store, row[0]).unlink(missing_ok=True)
            c.execute("DELETE FROM message_media WHERE id=?", (row[0],))
        used = storage_used(c)
        drafts = c.execute("SELECT coalesce(sum(size),0) FROM message_media WHERE user_id=? AND message_id=''", (str(user),)).fetchone()[0]
        if used + len(data) > 1024 * MB or drafts + len(data) > 100 * MB:
            raise ValueError("Armazenamento de anexos cheio. Revise os arquivos antes de continuar.")
        key = uuid.uuid4().hex
        path = path_for(store, key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(data)
            c.execute("INSERT INTO message_media(id,doctor_id,source_channel_id,contact_id,user_id,upload_request,name,mime,kind,size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, doctor, channel, contact, str(user), request, name, mime, kind, len(data), digest, time.time()))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return public(dict(c.execute("SELECT * FROM message_media WHERE id=?", (key,)).fetchone()))


def extract(payload):
    message = payload.get("message")
    for _ in range(5):
        if not isinstance(message, dict):
            return None
        if any(k in message for k in ("viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension")):
            return None
        for field, kind in (("imageMessage", "image"), ("videoMessage", "video"), ("audioMessage", "audio"), ("documentMessage", "document"), ("stickerMessage", "image")):
            value = message.get(field)
            if isinstance(value, dict):
                return None if value.get("viewOnce") else (kind, value, {field: value})
        wrapper = message.get("ephemeralMessage") or message.get("documentWithCaptionMessage") or {}
        message = wrapper.get("message") if isinstance(wrapper, dict) else None
    return None


def safe_name(value, fallback):
    if not isinstance(value, str):
        return fallback
    value = re.sub(r"[\x00-\x1f\x7f/\\]", "_", value).strip()[:180]
    return value or fallback


def register_received(c, message, channel, payload, *, legacy=False):
    found = extract(payload)
    if not found and not legacy:
        return
    if message.get("media_id"):
        if found:
            c.execute("UPDATE message_media SET provider_payload=? WHERE id=? AND download_state='remote'",
                (json.dumps({"key": payload["key"], "message": found[2]}), message["media_id"]))
        return
    kind, value, raw = found if found else (payload["kind"], {}, None)
    is_sticker = bool(raw and "stickerMessage" in raw or legacy and payload.get("is_sticker") is True)
    defaults = {"image": "imagem.jpg", "audio": "audio.ogg", "video": "video.mp4", "document": "arquivo"}
    mime = value.get("mimetype") if isinstance(value.get("mimetype"), str) else "image/webp" if is_sticker else "application/octet-stream"
    name = safe_name(value.get("fileName"), "figurinha.webp" if is_sticker else defaults[kind])
    size = value.get("fileLength", 0)
    if isinstance(size, dict):
        size = size.get("low", 0)
    size = size if type(size) is int and 0 <= size <= 2**53 else 0
    key = uuid.uuid4().hex
    stored = {"key": payload["key"], **({"message": raw} if raw else {})}
    c.execute("""INSERT OR IGNORE INTO message_media(id,doctor_id,source_channel_id,contact_id,user_id,upload_request,name,mime,kind,size,sha256,created_at,message_id,provider_payload,download_state,is_sticker)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'remote',?)""", (key, channel["doctor_id"], message["channel_id"], message["contact_id"], "external", "message:" + message["id"],
        name, mime[:100], kind, size, "", message["created_at"], message["id"], json.dumps(stored), int(is_sticker)))
    key = c.execute("SELECT id FROM message_media WHERE user_id='external' AND upload_request=?", ("message:" + message["id"],)).fetchone()[0]
    c.execute("UPDATE messages SET media_id=? WHERE id=?", (key, message["id"]))
    message["media_id"] = key


def attach(c, messages, *, legacy=False):
    if legacy:
        for message in messages:
            if message.get("media_id") or not message.get("provider_id"):
                continue
            match = re.match(r"^\[(Imagem|Audio|Video|Documento|Figurinha)\](?: |$)", message["body"])
            if match:
                channel = dict(c.execute("SELECT * FROM channels WHERE id=?", (message["channel_id"],)).fetchone())
                kind = {"Imagem": "image", "Figurinha": "image", "Audio": "audio", "Video": "video", "Documento": "document"}[match[1]]
                payload = {"kind": kind, "is_sticker": match[1] == "Figurinha", "key": {"id": message["provider_id"], "remoteJid": (message["recipient_phone"] or "") + "@s.whatsapp.net", "fromMe": message["direction"] == "out"}}
                register_received(c, message, channel, payload, legacy=True)
    ids = {m.get("media_id") for m in messages if m.get("media_id")}
    if not ids:
        return messages
    rows = c.execute("SELECT * FROM message_media WHERE id IN (" + ",".join("?" for _ in ids) + ")", tuple(ids)).fetchall()
    media = {r["id"]: public(dict(r)) for r in rows}
    for message in messages:
        if message.get("media_id") in media:
            message["media"] = media[message["media_id"]]
    from crm_audio import attach as attach_transcripts
    attach_transcripts(c,messages)
    return messages


def fetch_media(store, gateway, doctor, key):
    signature = (str(store.path), key)
    with FETCH_LOCK:
        if signature in FETCHING or not FETCH_GATE.acquire(blocking=False):
            raise ValueError("Anexo sendo preparado. Aguarde e tente novamente.")
        FETCHING.add(signature)
    try:
        with store.db() as c:
            row = store.owned(c, "message_media", key, doctor)
            if row["download_state"] == "ready":
                return public(row)
            channel, _ = store.conversation_contact(c, doctor, row["source_channel_id"], row["contact_id"])
        payload = json.loads(row["provider_payload"])
        if "message" not in payload:
            payload = gateway.media_message(channel, payload["key"]["id"])
        found = extract(payload)
        if not found:
            raise ValueError("Este anexo nao esta disponivel para arquivo. Midia de visualizacao unica nao e armazenada.")
        if found[0] != row["kind"] or bool("stickerMessage" in found[2]) != bool(row["is_sticker"]) or (payload.get("key") or {}).get("id") != json.loads(row["provider_payload"])["key"]["id"]:
            raise ValueError("Anexo nao corresponde a esta mensagem.")
        with store.db() as c:
            message = store.row(c, "messages", row["message_id"])
        remote = (payload.get("key") or {}).get("remoteJid", "")
        if isinstance(remote, str) and remote.endswith("@lid"):
            remote = payload["key"].get("remoteJidAlt", "")
        if not isinstance(remote, str) or not remote.endswith("@s.whatsapp.net") or remote.split("@", 1)[0].split(":", 1)[0] != message["recipient_phone"]:
            raise ValueError("Anexo de outra conversa.")
        maximum = MB if row["is_sticker"] else LIMITS[row["kind"]]
        response = gateway.download_media(channel, {"key": payload["key"], "message": found[2]}, maximum)
        encoded = response.get("base64") if isinstance(response, dict) else None
        if not isinstance(encoded, str) or len(encoded) > 4 * ((maximum + 2) // 3) + 256:
            raise ValueError("Anexo indisponivel ou maior que o limite permitido.")
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            raise ValueError("Anexo retornado pelo WhatsApp e invalido.") from None
        if not data or len(data) > maximum:
            raise ValueError("Anexo vazio ou maior que o limite permitido.")
        mime = response.get("mimetype", row["mime"])
        mime = mime.split(";", 1)[0] if isinstance(mime, str) else "application/octet-stream"
        if row["is_sticker"]:
            if mime not in {"image/webp", "application/octet-stream", ""}:
                raise ValueError("Formato de figurinha nao suportado.")
            mime = "image/webp"
        extensions = {value[1]: ext for ext, value in TYPES.items() if value[0] == row["kind"]}
        if row["kind"] != "document":
            ext = extensions.get(mime)
            if not ext:
                raise ValueError("Formato de midia nao suportado pelo visualizador.")
            name = Path(row["name"]).stem + ext
            _, mime = validate(name, row["kind"], data)
        else:
            name = safe_name(response.get("fileName"), row["name"])
            mime = TYPES.get(Path(name).suffix.lower(), ("document", "application/octet-stream"))[1]
        with store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            used = storage_used(c)
            if used + len(data) > 1024 * MB:
                raise ValueError("Armazenamento de anexos cheio.")
            path = path_for(store, key)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as target:
                    target.write(data)
                c.execute("UPDATE message_media SET name=?,mime=?,size=?,sha256=?,provider_payload='',download_state='ready' WHERE id=?",
                    (name, mime, len(data), hashlib.sha256(data).hexdigest(), key))
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return public(dict(c.execute("SELECT * FROM message_media WHERE id=?", (key,)).fetchone()))
    finally:
        with FETCH_LOCK:
            FETCHING.discard(signature)
            FETCH_GATE.release()


def for_send(store, item):
    with store.db() as c:
        row = store.row(c, "message_media", item["media_id"])
        if (row["message_id"], row["doctor_id"], row["contact_id"]) != (item["id"], item["doctor_id"], item["contact_id"]):
            raise ValueError("Anexo de outra conversa.")
    path = path_for(store, row.get("followup_asset_id") or row["id"])
    if path.is_symlink():
        raise ValueError("Anexo indisponivel.")
    with path.open("rb") as source:
        data = source.read(LIMITS[row["kind"]] + 1)
    if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("Anexo indisponivel ou alterado.")
    return {"kind": row["kind"], "mime": row["mime"], "name": row["name"], "base64": base64.b64encode(data).decode("ascii")}


def serve(handler, store, doctor, key):
    with store.db() as c:
        row = store.owned(c, "message_media", key, doctor)
        if not row["message_id"] and row["user_id"] != str(handler.current_user["id"]):
            raise ValueError("Anexo de outro usuario.")
    path = path_for(store, row.get("followup_asset_id") or key)
    return stream(handler, row, path)


def stream(handler, row, path):
    if not path.is_file() or path.is_symlink():
        raise LookupError("Anexo indisponivel.")
    size = path.stat().st_size
    start, end, code = 0, size - 1, 200
    requested = handler.headers.get("Range", "")
    if requested:
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
        if not match or int(match[1]) >= size or match[2] and int(match[2]) < int(match[1]):
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        start, end, code = int(match[1]), min(int(match[2]) if match[2] else size - 1, size - 1), 206
    handler.send_response(code)
    handler.send_header("Content-Type", row["mime"])
    handler.send_header("Content-Length", str(end - start + 1))
    disposition = "attachment" if row["kind"] == "document" else "inline"
    handler.send_header("Content-Disposition", disposition + "; filename*=UTF-8''" + quote(row["name"], safe=""))
    handler.send_header("Cache-Control", "private, no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Security-Policy", "sandbox; default-src 'none'")
    handler.send_header("Accept-Ranges", "bytes")
    if code == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    with path.open("rb") as source:
        source.seek(start)
        remaining = end - start + 1
        while remaining:
            block = source.read(min(64 * 1024, remaining))
            if not block:
                break
            handler.wfile.write(block)
            remaining -= len(block)


def receive_upload(handler, store, doctor, query):
    kind = query.get("kind", [""])[0]
    if kind not in LIMITS:
        raise ValueError("Tipo de anexo invalido.")
    channel, contact = query.get("channel", [""])[0], query.get("contact", [""])[0]
    with store.db() as c:
        store.conversation_contact(c, doctor, channel, contact)
    result = upload(store, doctor, channel, contact, handler.current_user["id"], handler.headers.get("X-Upload-Id", ""),
        unquote(handler.headers.get("X-File-Name", "")), kind, read_body(handler, LIMITS[kind]))
    handler.server.auth_store.audit_event("crm_media_upload", handler.current_user["id"], details={"clinic": "vielle", "doctor_id": doctor, "id": result["id"]})
    return handler.auth_json({"ok": True, "media": result})
