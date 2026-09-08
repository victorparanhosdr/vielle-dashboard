import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auth_store import AuthStore
from auth_http import LoginLimiter


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AuthStore(Path(self.temp.name) / "auth.sqlite3")
        self.store.initialize()
        self.password = "Temporary-test-password!"
        self.user_id = self.store.create_user("Teste", "teste", self.password)

    def test_session_persists_only_hash_and_updates_last_login(self):
        token = self.store.login("teste", self.password)
        user = self.store.session_user(token)
        self.assertEqual(user["id"], self.user_id)
        self.assertIsNotNone(user["last_login"])
        self.assertNotIn("password_hash", user)
        with self.store.connection() as conn:
            self.assertNotEqual(conn.execute("SELECT token_hash FROM sessions").fetchone()[0], token)
        reopened = AuthStore(self.store.path)
        reopened.initialize()
        self.assertEqual(reopened.session_user(token)["id"], self.user_id)

    def test_invalid_login_does_not_update_timestamp(self):
        self.assertIsNone(self.store.login("teste", "wrong"))
        self.assertIsNone(self.store.login("absent", self.password))
        self.assertIsNone(self.store.get_user_by_login("teste")["last_login"])

    def test_expiry_rotation_logout_and_tampering(self):
        token = self.store.login("teste", self.password)
        next_token = self.store.login("teste", self.password, token)
        self.assertNotEqual(next_token, token)
        self.assertIsNone(self.store.session_user(token))
        self.assertIsNone(self.store.session_user("x" * 43))
        self.assertIsNone(self.store.session_user("malformed"))
        self.store.logout(next_token)
        self.assertIsNone(self.store.session_user(next_token))
        token = self.store.login("teste", self.password)
        with self.store.connection() as conn:
            conn.execute("UPDATE sessions SET expires_at = 0")
        self.assertIsNone(self.store.session_user(token))

    def test_disable_and_reset_revoke_all_sessions(self):
        tokens = [self.store.login("teste", self.password) for _ in range(2)]
        self.store.set_user_active(self.user_id, False)
        self.assertIsNone(self.store.login("teste", self.password))
        self.store.set_user_active(self.user_id, True)
        for token in tokens:
            self.assertIsNone(self.store.session_user(token))
        token = self.store.login("teste", self.password)
        self.store.set_password(self.user_id, "Another-test-password!")
        self.assertIsNone(self.store.session_user(token))

    def test_phase1_migration_preserves_users(self):
        with self.store.connection() as conn:
            conn.execute("DROP TABLE sessions")
            conn.execute("PRAGMA user_version = 1")
        self.store.initialize()
        self.assertEqual(self.store.get_user_by_login("teste")["id"], self.user_id)
        self.assertIsNotNone(self.store.login("teste", self.password))

    def test_attempt_limiter(self):
        limiter = LoginLimiter()
        with patch("auth_http.time.monotonic", return_value=1000):
            for _ in range(10):
                self.assertTrue(limiter.allow("TESTE", "local"))
            self.assertFalse(limiter.allow("teste", "different-peer"))
        with patch("auth_http.time.monotonic", return_value=1601):
            self.assertTrue(limiter.allow("teste", "local"))
