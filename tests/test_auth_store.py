import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auth_store import AuthStore, auth_database_path, hash_password, verify_password


PASSWORD = "Local-test-only-2026!"


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = AuthStore(self.root / "auth" / "doc4docs_auth.sqlite3")
        self.store.initialize()

    def create(self, login="tester", **kwargs):
        return self.store.create_user("Test User", login, PASSWORD, **kwargs)

    def test_empty_database_and_idempotent_initialization(self):
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM users").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 4)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"users", "sessions", "user_clinics", "user_clinic_permissions", "access_profiles", "audit_log", "sqlite_sequence"})
        self.create()
        self.store.initialize()
        self.assertTrue(self.store.validate_password("tester", PASSWORD))

    def test_hash_salts_and_malformed_hashes(self):
        first, second = hash_password(PASSWORD), hash_password(PASSWORD)
        self.assertNotEqual(first, second)
        self.assertNotIn(PASSWORD, first)
        self.assertTrue(verify_password(PASSWORD, first))
        self.assertFalse(verify_password("wrong", first))
        for malformed in (None, "", "cleartext", "pbkdf2_sha256$999999999$00$00"):
            self.assertFalse(verify_password(PASSWORD, malformed))

    def test_lookup_normalizes_login_and_enforces_unique(self):
        user_id = self.create("  Tester@Example.COM ")
        user = self.store.get_user_by_login("TESTER@example.com")
        self.assertEqual(user["id"], user_id)
        self.assertEqual(user["login"], "tester@example.com")
        self.assertEqual(user["status"], "active")
        self.assertEqual(user["is_master"], 0)
        self.assertIsNone(user["last_login"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.create("tester@example.com")
        self.assertTrue(self.store.validate_password("tester@example.com", PASSWORD))

    def test_validation_has_no_login_timestamp_side_effect(self):
        user_id = self.create()
        self.assertTrue(self.store.validate_password("tester", PASSWORD))
        self.assertIsNone(self.store.get_user_by_login("tester")["last_login"])
        self.assertTrue(self.store.update_last_login(user_id))
        self.assertTrue(self.store.get_user_by_login("tester")["last_login"].endswith("+00:00"))

    def test_inactive_user_and_manual_password_reset(self):
        user_id = self.create()
        self.assertTrue(self.store.set_user_active(user_id, False))
        self.assertFalse(self.store.validate_password("tester", PASSWORD))
        self.assertFalse(self.store.update_last_login(user_id))
        self.assertTrue(self.store.set_password(user_id, "Replacement-test-2026!"))
        self.assertEqual(self.store.get_user_by_login("tester")["status"], "inactive")
        self.assertTrue(self.store.set_user_active(user_id, True))
        self.assertFalse(self.store.validate_password("tester", PASSWORD))
        self.assertTrue(self.store.validate_password("tester", "Replacement-test-2026!"))

    def test_bad_input_does_not_create_user(self):
        for password in ("short", "", None, "x" * 1025):
            with self.assertRaises(ValueError):
                self.store.create_user("Test", "tester", password)
        for login in ("", "with space", "bad\x00login", None):
            with self.assertRaises(ValueError):
                self.create(login)
        with self.assertRaises(ValueError):
            self.create(status="invalid")
        with self.assertRaises(ValueError):
            self.create(is_master="false")
        self.assertIsNone(self.store.get_user_by_login("tester"))

    def test_invalid_reset_preserves_existing_password(self):
        user_id = self.create()
        with self.assertRaises(ValueError):
            self.store.set_password(user_id, "short")
        self.assertTrue(self.store.validate_password("tester", PASSWORD))

    def test_missing_users_return_false(self):
        self.assertIsNone(self.store.get_user_by_login("missing"))
        self.assertFalse(self.store.validate_password("missing", PASSWORD))
        self.assertFalse(self.store.set_user_active(9999, False))
        self.assertFalse(self.store.update_last_login(9999))
        self.assertFalse(self.store.set_password(9999, PASSWORD))

    def test_reopen_preserves_users_and_clinic_files(self):
        clinics = [self.root / name for name in ("kommo_report.sqlite3", "kommo_report_inspire.sqlite3", "kommo_report_carla.sqlite3")]
        for file in clinics:
            file.write_bytes(b"unchanged clinic fixture")
        self.create()
        reopened = AuthStore(self.store.path)
        reopened.initialize()
        self.assertTrue(reopened.validate_password("tester", PASSWORD))
        for file in clinics:
            self.assertEqual(file.read_bytes(), b"unchanged clinic fixture")

    def test_independent_connections_support_concurrent_writes(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(self.create, [f"user{n}" for n in range(4)]))
        self.assertEqual(len(set(ids)), 4)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM users").fetchone()[0], 4)

    def test_storage_path_and_no_silent_fallback(self):
        volume = self.root / "volume"
        explicit = self.root / "data"
        self.assertEqual(auth_database_path(self.root, {}), self.root / "doc4docs_auth.sqlite3")
        self.assertEqual(auth_database_path(self.root, {"RAILWAY_VOLUME_MOUNT_PATH": str(volume)}), volume / "doc4docs_auth.sqlite3")
        self.assertEqual(auth_database_path(self.root, {"DATA_DIR": str(explicit), "RAILWAY_VOLUME_MOUNT_PATH": str(volume)}), explicit / "doc4docs_auth.sqlite3")
        with self.assertRaises(RuntimeError):
            auth_database_path(self.root, {"RAILWAY_PROJECT_ID": "test"})
        blocked = self.root / "not-a-directory"
        blocked.write_text("fixture")
        path = auth_database_path(self.root, {"DATA_DIR": str(blocked)})
        with self.assertRaises(OSError):
            AuthStore(path).initialize()
        self.assertFalse((self.root / "doc4docs_auth.sqlite3").exists())

    def test_newer_schema_is_not_downgraded(self):
        with self.store.connection() as conn:
            conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            self.store.initialize()
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 99)


if __name__ == "__main__":
    unittest.main()
