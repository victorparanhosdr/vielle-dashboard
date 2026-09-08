import sqlite3
import tempfile
import unittest
from pathlib import Path

from auth_store import AuthStore
from clinic_catalog import SUPPORTED_CLINICS


class ClinicMembershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "auth.sqlite3"
        self.store = AuthStore(self.path)
        self.store.initialize()

    def test_default_denied_and_explicit_memberships_persist(self):
        uid = self.store.create_user("Test", "test", "Temporary-test-password!")
        self.assertEqual(self.store.allowed_clinics(uid), [])
        self.store.update_user(uid, "Test", "test", clinic_keys=["inspire", "carla", "inspire"])
        reopened = AuthStore(self.path)
        reopened.initialize()
        self.assertEqual(reopened.allowed_clinics(uid), ["inspire", "carla"])
        self.assertFalse(reopened.user_can_access_clinic(uid, "vielle"))
        self.store.update_user(uid, "Renamed", "test")
        self.assertEqual(self.store.get_public_user(uid)["clinic_keys"], ["inspire", "carla"])
        self.assertEqual(self.store.list_users()["users"][0]["clinic_keys"], ["inspire", "carla"])

    def test_master_all_and_inactive_denied(self):
        uid = self.store.create_first_master("Master", "master", "Temporary-test-password!")
        self.assertEqual(self.store.allowed_clinics(uid), list(SUPPORTED_CLINICS))
        self.assertFalse(self.store.user_can_access_clinic(uid, "unknown"))
        with self.assertRaises(ValueError):
            self.store.update_user(uid, "Master", "master", clinic_keys=[])
        common = self.store.create_user("Common", "common", "Temporary-test-password!", clinic_keys=["vielle"])
        self.store.set_user_active(common, False)
        self.assertEqual(self.store.allowed_clinics(common), [])

    def test_invalid_memberships_and_duplicate_login_rollback(self):
        uid = self.store.create_user("Test", "test", "Temporary-test-password!", clinic_keys=["vielle"])
        self.store.create_user("Other", "other", "Temporary-test-password!")
        for keys in (["unknown"], "vielle", [None], [{}]):
            with self.assertRaises(ValueError):
                self.store.update_user(uid, "Changed", "test", clinic_keys=keys)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.update_user(uid, "Changed", "other", clinic_keys=["carla"])
        self.assertEqual(self.store.allowed_clinics(uid), ["vielle"])
        self.assertEqual(self.store.get_public_user(uid)["nome"], "Test")

    def test_v2_migration_preserves_users_hashes_sessions(self):
        uid = self.store.create_user("Test", "test", "Temporary-test-password!")
        token = self.store.login("test", "Temporary-test-password!")
        before = self.store.get_user_by_login("test")
        with self.store.connection() as conn:
            conn.execute("DROP TABLE user_clinics")
            conn.execute("PRAGMA user_version = 2")
        self.store.initialize()
        self.store.initialize()
        self.assertEqual(self.store.get_user_by_login("test"), before)
        self.assertEqual(self.store.session_user(token)["id"], uid)
        self.assertEqual(self.store.allowed_clinics(uid), [])
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 4)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO user_clinics VALUES (999, 'vielle', 'now')")


if __name__ == "__main__":
    unittest.main()
