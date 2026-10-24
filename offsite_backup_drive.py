"""Explicit, resumable uploads of already encrypted backups. No worker starts here."""
from dataclasses import dataclass
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from offsite_backup import BackupError, BLOCK, digest_file, regular_open, signature

API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
TOKEN = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"
MARKER = {"doc4docs_kind": "offsite_backup_v1"}
FIELDS = ("id,name,mimeType,parents,trashed,driveId,shared,owners(emailAddress,me),"
          "permissions(type,role,emailAddress,deleted),size,md5Checksum,sha256Checksum,appProperties")
CHUNK = 8 * BLOCK


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict
    data: bytes = b""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def checked_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", value):
        raise BackupError("invalid_drive_id")
    return value


def allowed_url(value, *, session=False):
    if not isinstance(value, str) or len(value) > 8192:
        raise BackupError("unsafe_google_url")
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.username or parts.password or parts.fragment
            or parts.netloc not in {"www.googleapis.com", "openidconnect.googleapis.com", "oauth2.googleapis.com"}):
        raise BackupError("unsafe_google_url")
    if session:
        query = parse_qs(parts.query)
        if (parts.netloc != "www.googleapis.com" or parts.path != "/upload/drive/v3/files"
                or query.get("uploadType") != ["resumable"] or len(query.get("upload_id", [])) != 1
                or not query["upload_id"][0]):
            raise BackupError("unsafe_upload_session")
    elif not (value == USERINFO or value == TOKEN or parts.netloc == "www.googleapis.com"
              and (parts.path == "/upload/drive/v3/files" or parts.path == "/drive/v3/files"
                   or re.fullmatch(r"/drive/v3/files/[A-Za-z0-9_-]{10,200}", parts.path))):
        raise BackupError("unsafe_google_url")
    return value


def request(url, method="GET", data=None, headers=None):
    """No ambient proxies, redirects, raw response logging, or implicit retries."""
    allowed_url(url)
    req = Request(url, method=method, data=data, headers=headers or {})
    try:
        try:
            response = build_opener(ProxyHandler({}), NoRedirect).open(req, timeout=30)
        except HTTPError as exc:
            response = exc
        with response:
            code = response.code
            content = response.read(BLOCK + 1) if code in {200, 201} else b""
            if len(content) > BLOCK:
                raise BackupError("google_response_limit")
            return Reply(code, {k.lower(): v for k, v in response.headers.items()}, content)
    except (URLError, TimeoutError, OSError):
        raise BackupError("google_response_uncertain") from None


def decoded(reply):
    if reply.status not in {200, 201}:
        raise BackupError("google_http_" + str(reply.status))
    try:
        value = json.loads(reply.data)
    except (ValueError, UnicodeError):
        raise BackupError("invalid_google_response") from None
    if not isinstance(value, dict):
        raise BackupError("invalid_google_response")
    return value


def private_metadata(value, owner, *, folder=False):
    owners, permissions = value.get("owners"), value.get("permissions")
    if (value.get("trashed") is not False or value.get("shared") is not False or value.get("driveId")
            or not isinstance(owners, list) or len(owners) != 1 or not isinstance(owners[0], dict)
            or owners[0].get("emailAddress", "").casefold() != owner or owners[0].get("me") is not True
            or not isinstance(permissions, list) or len(permissions) != 1 or not isinstance(permissions[0], dict)
            or permissions[0].get("type") != "user" or permissions[0].get("role") != "owner"
            or permissions[0].get("emailAddress", "").casefold() != owner or permissions[0].get("deleted")
            or value.get("appProperties", {}).get("doc4docs_kind") != MARKER["doc4docs_kind"]
            or folder and value.get("mimeType") != FOLDER_MIME):
        raise BackupError("drive_destination_not_private")


class Drive:
    def __init__(self, token_supplier, owner, *, transport=request):
        if not isinstance(owner, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+", owner):
            raise BackupError("missing_backup_owner")
        self.owner, self.token_supplier, self.transport = owner.casefold(), token_supplier, transport

    def call(self, url, method="GET", data=None, headers=None):
        allowed_url(url)
        token = self.token_supplier()
        if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
            raise BackupError("invalid_google_authorization")
        return self.transport(url, method, data, {"Authorization": "Bearer " + token,
                              "Accept": "application/json", **(headers or {})})

    def identity(self):
        profile = decoded(self.call(USERINFO))
        if (profile.get("email_verified") is not True or not profile.get("sub")
                or profile.get("email", "").casefold() != self.owner):
            raise BackupError("backup_owner_mismatch")

    def new_id(self):
        value = decoded(self.call(API + "/generateIds?count=1&space=drive&type=files"))
        if not isinstance(value.get("ids"), list) or len(value["ids"]) != 1:
            raise BackupError("invalid_google_response")
        return checked_id(value["ids"][0])

    def metadata(self, ident):
        reply = self.call(API + "/" + checked_id(ident) + "?" + urlencode({"fields": FIELDS}))
        return None if reply.status == 404 else decoded(reply)

    def folder(self, ident):
        """The caller must durably save a pre-generated ID before calling this."""
        self.identity()
        checked_id(ident)
        value = self.metadata(ident)
        if value is None:
            reply = self.call(API, "POST", json.dumps({"id": ident, "name": "DOC4DOCS - Backups cifrados do CRM",
                "mimeType": FOLDER_MIME, "appProperties": MARKER}).encode(), {"Content-Type": "application/json"})
            if reply.status not in {200, 201, 409}:
                decoded(reply)
            value = self.metadata(ident)
        if not value or value.get("id") != ident:
            raise BackupError("folder_creation_unconfirmed")
        private_metadata(value, self.owner, folder=True)
        return value

    def confirmed_file(self, ident, folder, expected):
        value = self.metadata(ident)
        if value is None:
            return None
        private_metadata(value, self.owner)
        if (value.get("id") != ident or value.get("parents") != [folder]
                or value.get("mimeType") != "application/octet-stream"
                or value.get("size") != str(expected["size"])
                or value.get("md5Checksum") != expected["md5"]
                or value.get("sha256Checksum") and value["sha256Checksum"] != expected["sha256"]
                or value.get("appProperties", {}).get("cipher_sha256") != expected["sha256"]):
            raise BackupError("drive_file_mismatch")
        return {"id": ident, "verified": True, "size": expected["size"], "sha256": expected["sha256"]}

    @staticmethod
    def offset(reply, size):
        if reply.status != 308:
            raise BackupError("unexpected_upload_state")
        value = reply.headers.get("range", "")
        if not value:
            return 0
        match = re.fullmatch(r"bytes=0-([0-9]+)", value)
        offset = int(match[1]) + 1 if match else -1
        if not 0 <= offset <= size:
            raise BackupError("invalid_upload_range")
        return offset

    def upload(self, path, folder, ident, expected, checkpoint, *, session="", seconds=1200):
        """Persist session before sending; never retry an uncertain write blindly."""
        checked_id(folder)
        checked_id(ident)
        if (type(expected.get("size")) is not int or not 21 < expected["size"] <= 8 * 1024**3
                or not re.fullmatch(r"[a-f0-9]{64}", expected.get("sha256", ""))
                or not re.fullmatch(r"[a-f0-9]{32}", expected.get("md5", ""))):
            raise BackupError("invalid_ciphertext_receipt")
        with regular_open(path)[0] as source:
            header = b"age-encryption.org/v1\n"
            if source.read(len(header)) != header:
                raise BackupError("not_an_age_ciphertext")
        self.identity()
        folder_meta = self.metadata(folder)
        if not folder_meta:
            raise BackupError("missing_backup_folder")
        private_metadata(folder_meta, self.owner, folder=True)
        if digest_file(path) != {k: expected[k] for k in ("size", "md5", "sha256")} or expected["size"] <= 0:
            raise BackupError("ciphertext_changed")
        existing = self.confirmed_file(ident, folder, expected)
        if existing:
            checkpoint({"session": "", "offset": expected["size"], "verified": True})
            return existing
        size, deadline = expected["size"], time.monotonic() + seconds
        if session:
            allowed_url(session, session=True)
            reply = self.call(session, "PUT", b"", {"Content-Length": "0", "Content-Range": "bytes */" + str(size)})
            if reply.status in {200, 201}:
                result = self.confirmed_file(ident, folder, expected)
                if not result:
                    raise BackupError("upload_unconfirmed")
                checkpoint({"session": "", "offset": size, "verified": True})
                return result
            if reply.status == 404:
                session = ""
            elif reply.status != 308:
                decoded(reply)
        if not session:
            metadata = {"id": ident, "name": "doc4docs-" + ident + ".tar.gz.age", "parents": [folder],
                        "mimeType": "application/octet-stream", "appProperties": MARKER | {"cipher_sha256": expected["sha256"]}}
            reply = self.call(UPLOAD + "?uploadType=resumable", "POST", json.dumps(metadata).encode(),
                {"Content-Type": "application/json", "X-Upload-Content-Type": "application/octet-stream",
                 "X-Upload-Content-Length": str(size)})
            if reply.status not in {200, 201}:
                decoded(reply)
            session = allowed_url(reply.headers.get("location", ""), session=True)
            offset = 0
        else:
            offset = self.offset(reply, size)
        checkpoint({"session": session, "offset": offset, "verified": False})
        handle, initial = regular_open(path)
        with handle:
            while offset < size:
                if time.monotonic() > deadline or signature(initial) != signature(os.fstat(handle.fileno())):
                    raise BackupError("upload_local_limit")
                handle.seek(offset)
                chunk = handle.read(min(CHUNK, size - offset))
                if not chunk:
                    raise BackupError("ciphertext_changed")
                end = offset + len(chunk)
                reply = self.call(session, "PUT", chunk, {"Content-Type": "application/octet-stream",
                    "Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end - 1}/{size}"})
                if reply.status in {200, 201}:
                    if end != size:
                        raise BackupError("premature_upload_completion")
                    offset = size
                elif reply.status == 308:
                    received = self.offset(reply, size)
                    if not offset < received <= end:
                        raise BackupError("upload_not_progressing")
                    offset = received
                else:
                    decoded(reply)
                checkpoint({"session": session, "offset": offset, "verified": False})
            if signature(initial) != signature(os.fstat(handle.fileno())):
                raise BackupError("ciphertext_changed")
        # Recheck inherited access as well as the provider's content checksum.
        private_metadata(self.metadata(folder) or {}, self.owner, folder=True)
        result = self.confirmed_file(ident, folder, expected)
        if not result:
            raise BackupError("upload_unconfirmed")
        checkpoint({"session": "", "offset": size, "verified": True})
        return result
