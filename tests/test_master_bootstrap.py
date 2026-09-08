from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from auth_store import AuthStore


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AuthStore(Path(self.temp.name) / "auth.sqlite3")
        self.store.initialize()

    def test_explicit_master_creation_and_repeat_rejected_even_if_inactive(self):
        self.assertIsNone(self.store.get_user_by_login("master"))
        uid = self.store.create_first_master("Master", "master", "Master-test-password!")
        user = self.store.get_user_by_login("master")
        self.assertEqual(user["is_master"], 1)
        self.assertNotEqual(user["password_hash"], "Master-test-password!")
        with self.store.connection() as conn:
            conn.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (uid,))
        with self.assertRaises(ValueError):
            self.store.create_first_master("Other", "other", "Master-test-password!")
        self.assertIsNone(self.store.get_user_by_login("other"))

    def test_existing_common_user_is_never_promoted_by_bootstrap(self):
        uid = self.store.create_user("Common", "master", "Common-test-password!")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_first_master("Master", "master", "Master-test-password!")
        user = self.store.get_user_by_login("master")
        self.assertEqual(user["id"], uid)
        self.assertEqual(user["is_master"], 0)
        self.assertTrue(self.store.validate_password("master", "Common-test-password!"))

    def test_concurrent_bootstrap_creates_exactly_one_master(self):
        def create(login):
            try:
                return self.store.create_first_master("Master", login, "Master-test-password!")
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(create, ("master1", "master2")))
        self.assertEqual(sum(uid is not None for uid in result), 1)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM users WHERE is_master = 1").fetchone()[0], 1)
