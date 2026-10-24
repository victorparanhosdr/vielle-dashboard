"""Read-only use of one explicitly selected existing Google connection."""
from contextlib import closing
import hashlib
import json
import re
import time
from urllib.parse import urlencode

from offsite_backup import BackupError, regular_open, sqlite_readonly
from offsite_backup_drive import TOKEN, decoded, request

SCOPE = "https://www.googleapis.com/auth/drive.file"


class AccountToken:
    def __init__(self, database, keyfile, account_id, owner, client_id, client_secret, *, transport=request):
        if not re.fullmatch(r"[a-f0-9]{32}", account_id or "") or not owner or not client_id or not client_secret:
            raise BackupError("missing_backup_google_configuration")
        self.database, self.keyfile, self.account_id = database, keyfile, account_id
        self.owner, self.client_id, self.client_secret = owner.casefold(), client_id, client_secret
        self.transport, self.cache = transport, None

    def __call__(self):
        # Do not construct FormStore: its initialization can mutate queues/schemas.
        with closing(sqlite_readonly(self.database)) as c:
            row = c.execute("SELECT google_email,google_scopes,google_token FROM form_google_accounts WHERE id=?",
                            (self.account_id,)).fetchone()
        if not row or row[0].casefold() != self.owner or SCOPE not in row[1].split() or not row[2]:
            raise BackupError("backup_google_connection_unavailable")
        if not isinstance(row[2], str) or len(row[2]) > 32768:
            raise BackupError("invalid_backup_google_connection")
        with regular_open(self.keyfile)[0] as key:
            secret = key.read(1024)
        if len(secret) != 44:
            raise BackupError("invalid_backup_google_connection")
        fingerprint = hashlib.sha256(secret + row[2].encode()).digest()
        if self.cache and self.cache[0] == fingerprint and self.cache[2] > time.time() + 60:
            return self.cache[1]
        try:
            from cryptography.fernet import Fernet
            value = Fernet(secret).decrypt(row[2].encode())
            if len(value) > 16384:
                raise ValueError()
            token = json.loads(value)
            if not isinstance(token, dict) or not isinstance(token.get("refresh_token"), str):
                raise ValueError()
        except Exception:
            raise BackupError("invalid_backup_google_connection") from None
        # Refresh server-side without rewriting the shared forms account or queues.
        payload = urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                             "refresh_token": token["refresh_token"], "grant_type": "refresh_token"}).encode()
        fresh = decoded(self.transport(TOKEN, "POST", payload, {"Content-Type": "application/x-www-form-urlencoded"}))
        access = fresh.get("access_token")
        if (not isinstance(access, str) or not 1 <= len(access) <= 16384 or "\r" in access or "\n" in access
                or fresh.get("scope") is not None and SCOPE not in fresh["scope"].split()
                or fresh.get("refresh_token") not in {None, token["refresh_token"]}
                or type(fresh.get("expires_in")) is not int or not 60 < fresh["expires_in"] <= 86400):
            raise BackupError("invalid_google_authorization")
        self.cache = (fingerprint, access, time.time() + fresh["expires_in"])
        return access
