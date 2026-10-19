"""Private, doctor/channel-scoped WhatsApp avatars, never patient identity evidence."""
from hashlib import sha256
from io import BytesIO
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from crm_gateway import NoRedirect

FETCHES = threading.BoundedSemaphore(2)
MAX_BYTES = 512 * 1024


def schema(store):
    with store.db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS contact_avatars(
            channel_id TEXT NOT NULL REFERENCES channels(id),contact_id TEXT NOT NULL REFERENCES contacts(id),
            phone TEXT NOT NULL,state TEXT NOT NULL,checked_at REAL NOT NULL,version TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(channel_id,contact_id))""")


def peer(store, c, doctor, contact_id, channel_id=""):
    store.row(c, "doctors", doctor)
    if not c.execute("SELECT 1 FROM leads WHERE doctor_id=? AND contact_id=?", (doctor, contact_id)).fetchone():
        raise LookupError()
    contact = store.row(c, "contacts", contact_id)
    if channel_id:
        channel = store.owned(c, "channels", channel_id, doctor)
    else:
        channel = c.execute("""SELECT ch.* FROM channels ch WHERE ch.doctor_id=? AND ch.state='open'
            ORDER BY COALESCE((SELECT MAX(m.created_at) FROM messages m WHERE m.channel_id=ch.id AND m.contact_id=?),0) DESC,ch.id LIMIT 1""",
                            (doctor, contact_id)).fetchone()
        channel = dict(channel) if channel else None
    number = store.conversation_peer(c, channel["id"], contact_id, contact["phone"]) if channel else contact["phone"]
    return channel, number


def filename(store, channel, contact, number):
    key = sha256(f"{channel}\0{contact}\0{number}".encode()).hexdigest()
    return store.path.parent / "avatars" / (key + ".jpg")


def download(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
            or not (host in {"pps.whatsapp.net", "mmg.whatsapp.net"} or host.endswith(".fbcdn.net"))):
        raise ValueError("Origem de foto nao permitida.")
    # Do not forward the Evolution API key or accept provider-controlled redirects.
    request = Request(url, headers={"Accept": "image/jpeg,image/png,image/webp"})
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        if response.headers.get_content_type() not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("Formato de foto nao permitido.")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Foto muito grande.")
    from PIL import Image, ImageOps
    with Image.open(BytesIO(raw)) as original:
        if original.width * original.height > 4_000_000 or original.format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError("Dimensoes de foto invalidas.")
        image = ImageOps.fit(ImageOps.exif_transpose(original).convert("RGB"), (160, 160))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
        return output.getvalue()


def result(store, doctor, channel, contact, number, row):
    path = filename(store, channel["id"], contact, number)
    ready = row and row["phone"] == number and row["version"] and path.is_file()
    return {"state": row["state"] if row else "missing", "url": "/api/crm/avatar?" + urlencode({
        "clinic": "vielle", "doctor": doctor, "channel": channel["id"], "contact": contact,
        "v": row["version"]}) if ready else "", "retry_after": 60 if row and row["state"] == "loading" else 0}


def refresh(store, gateway, doctor, contact_id, channel_id="", *, fetch=download):
    schema(store)
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        channel, number = peer(store, c, doctor, contact_id, channel_id)
        if not channel or channel["state"] != "open" or not gateway.configured:
            return {"state": "unavailable", "url": ""}
        row = c.execute("SELECT * FROM contact_avatars WHERE channel_id=? AND contact_id=?", (channel["id"], contact_id)).fetchone()
        if row and row["phone"] == number:
            ttl = 60 if row["state"] == "loading" else 900 if row["state"] == "unavailable" else 86400
            if time.time() - row["checked_at"] < ttl:
                return result(store, doctor, channel, contact_id, number, row)
        if not FETCHES.acquire(blocking=False):
            return {"state": "loading", "url": "", "retry_after": 15}
        try:
            c.execute("""INSERT INTO contact_avatars(channel_id,contact_id,phone,state,checked_at) VALUES(?,?,?,'loading',?)
                ON CONFLICT(channel_id,contact_id) DO UPDATE SET phone=excluded.phone,state='loading',checked_at=excluded.checked_at,
                version=CASE WHEN contact_avatars.phone=excluded.phone THEN contact_avatars.version ELSE '' END""",
                      (channel["id"], contact_id, number, time.time()))
        except Exception:
            FETCHES.release()
            raise
    try:
        try:
            url = gateway.profile_picture(channel, number)
            if url:
                body = fetch(url)
                path = filename(store, channel["id"], contact_id, number)
                path.parent.mkdir(mode=0o700, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(body)
                temporary.replace(path)
                version, state = sha256(body).hexdigest()[:20], "ready"
            else:
                version, state = "", "missing"
                filename(store, channel["id"], contact_id, number).unlink(missing_ok=True)
        except Exception:
            version, state = row["version"] if row and row["phone"] == number else "", "unavailable"
        with store.db() as c:
            c.execute("UPDATE contact_avatars SET state=?,checked_at=?,version=? WHERE channel_id=? AND contact_id=? AND phone=?",
                      (state, time.time(), version, channel["id"], contact_id, number))
            saved = c.execute("SELECT * FROM contact_avatars WHERE channel_id=? AND contact_id=?", (channel["id"], contact_id)).fetchone()
        return result(store, doctor, channel, contact_id, number, saved)
    finally:
        FETCHES.release()


def serve(handler, store, doctor, contact, channel_id):
    schema(store)
    with store.db() as c:
        channel, number = peer(store, c, doctor, contact, channel_id)
        if not channel:
            raise LookupError()
        row = c.execute("SELECT * FROM contact_avatars WHERE channel_id=? AND contact_id=?", (channel_id, contact)).fetchone()
    if not row or row["phone"] != number or not row["version"]:
        raise LookupError()
    path = filename(store, channel_id, contact, number)
    if not path.is_file():
        raise LookupError()
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "image/jpeg")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "private, no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)
