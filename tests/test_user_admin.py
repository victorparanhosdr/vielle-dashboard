from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from auth_store import AuthStore


class UserAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=AuthStore(Path(self.temp.name)/"auth.sqlite3")
        self.store.initialize()
        self.master=self.store.create_first_master("Master","master","Master-test-password!")
        self.user=self.store.create_user("Equipe","equipe","Equipe-test-password!")

    def test_list_search_status_pagination_and_safe_fields(self):
        result=self.store.list_users(page_size=1)
        self.assertEqual(result["total"],2)
        self.assertEqual(len(result["users"]),1)
        self.assertNotIn("password_hash",result["users"][0])
        self.assertEqual(self.store.list_users("equipe")["total"],1)
        self.assertEqual(self.store.list_users("%")["total"],0)
        self.store.set_user_active(self.user,False)
        self.assertEqual(self.store.list_users(status="inactive")["users"][0]["id"],self.user)
        self.assertNotEqual(result["users"][0]["id"],self.store.list_users(page=2,page_size=1)["users"][0]["id"])

    def test_identity_edit_preserves_password_role_and_dates(self):
        token=self.store.login("equipe","Equipe-test-password!")
        before=self.store.get_user_by_login("equipe")
        self.assertTrue(self.store.update_user(self.user,"Equipe Nova","equipe.nova"))
        after=self.store.get_user_by_login("equipe.nova")
        for field in ("password_hash","is_master","created_at","last_login","status"):
            self.assertEqual(after[field],before[field])
        self.assertIsNone(self.store.session_user(token))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.update_user(self.user,"Não salvar","MASTER")
        self.assertEqual(self.store.get_user_by_login("equipe.nova")["nome"],"Equipe Nova")

    def test_last_active_master_cannot_be_disabled(self):
        token=self.store.login("master","Master-test-password!")
        with self.assertRaises(ValueError):
            self.store.set_user_active(self.master,False)
        self.assertIsNotNone(self.store.session_user(token))

    def test_concurrent_deactivation_preserves_one_active_master(self):
        other=self.store.create_user("Other","other","Other-test-password!",is_master=True)
        def disable(uid):
            try:return self.store.set_user_active(uid,False)
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(disable,(self.master,other)))
        self.assertEqual(results.count(True),1)
        self.assertEqual(sum(u["is_master"] for u in self.store.list_users(status="active")["users"]),1)
