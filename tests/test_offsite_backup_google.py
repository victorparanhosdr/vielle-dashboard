"""Dummy encrypted OAuth rows; never open the application's database."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import unittest

from cryptography.fernet import Fernet

from offsite_backup import BackupError
from offsite_backup_drive import Reply, TOKEN
from offsite_backup_google import AccountToken, SCOPE


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(os.environ["BACKUP_TEST_ROOT"]) / "account-case"
        self.work.mkdir()
        self.database, self.key = self.work / "accounts.sqlite3", self.work / "oauth.key"
        key = Fernet.generate_key()
        self.key.write_bytes(key)
        self.cipher = Fernet(key).encrypt(json.dumps({"refresh_token": "dummy-refresh-only"}).encode()).decode()
        self.ident, self.owner = "a" * 32, "owner@example.invalid"
        with sqlite3.connect(self.database) as c:
            c.execute("CREATE TABLE form_google_accounts(id TEXT,google_email TEXT,google_scopes TEXT,google_token TEXT)")
            c.execute("INSERT INTO form_google_accounts VALUES(?,?,?,?)", (self.ident, self.owner, SCOPE, self.cipher))
            c.execute("INSERT INTO form_google_accounts VALUES(?,?,?,?)", ("b" * 32, "other@example.invalid", SCOPE, self.cipher))
        self.calls = []
        self.provider = AccountToken(self.database, self.key, self.ident, self.owner,
                                     "dummy-client", "dummy-secret", transport=self.transport)

    def tearDown(self):
        shutil.rmtree(self.work)

    def transport(self, url, method, data, headers):
        self.calls.append((url, method, data, headers))
        return Reply(200, {}, json.dumps({"access_token": "dummy-access-only", "expires_in": 3600, "scope": SCOPE}).encode())

    def test_refresh_and_cache_do_not_modify_shared_google_database(self):
        before = self.database.read_bytes()
        self.assertEqual(self.provider(), "dummy-access-only")
        self.assertEqual(self.provider(), "dummy-access-only")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], TOKEN)
        self.assertNotIn("Authorization", self.calls[0][3])
        self.assertEqual(self.database.read_bytes(), before)

    def test_revoked_connection_invalidates_cached_access(self):
        self.provider()
        with sqlite3.connect(self.database) as c:
            c.execute("UPDATE form_google_accounts SET google_token='' WHERE id=?", (self.ident,))
        with self.assertRaisesRegex(BackupError, "unavailable"):
            self.provider()
        self.assertEqual(len(self.calls), 1)

    def test_no_fallback_to_another_connected_account(self):
        self.provider.account_id = "c" * 32
        with self.assertRaisesRegex(BackupError, "unavailable"):
            self.provider()
        self.assertEqual(self.calls, [])

    def test_wrong_owner_or_missing_drive_scope_fails_before_refresh(self):
        for field, value in (("google_email", "other@example.invalid"), ("google_scopes", "openid email")):
            with sqlite3.connect(self.database) as c:
                c.execute("UPDATE form_google_accounts SET google_email=?,google_scopes=? WHERE id=?", (self.owner, SCOPE, self.ident))
                c.execute("UPDATE form_google_accounts SET " + field + "=? WHERE id=?", (value, self.ident))
            with self.assertRaisesRegex(BackupError, "unavailable"):
                self.provider()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
