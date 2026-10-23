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

    def test_explicit_promotion_preserves_identity_revokes_sessions_and_audits(self):
        token=self.store.login("equipe","Equipe-test-password!")
        before=self.store.get_user_by_login("equipe")
        self.store.promote_to_master(self.user,actor_id=self.master,expected_login="equipe")
        after=self.store.get_user_by_login("equipe")
        self.assertTrue(after["is_master"])
        for key in ("password_hash","nome","login","created_at","status"):
            self.assertEqual(before[key],after[key])
        self.assertIsNone(self.store.session_user(token))
        public=self.store.get_public_user(self.user)
        self.assertTrue(public["permissions"]["vielle"])
        self.assertTrue(all(p=="management" for p in public["team_profiles"].values()))
        self.assertEqual(len(self.store.list_audit(action="user_promoted_master")["events"]),1)
        self.store.promote_to_master(self.user,actor_id=self.master,expected_login="equipe")
        self.assertEqual(len(self.store.list_audit(action="user_promoted_master")["events"]),1)

    def test_promotion_requires_active_master_and_matching_active_target(self):
        with self.assertRaises(ValueError):
            self.store.promote_to_master(self.user,actor_id=self.user,expected_login="equipe")
        with self.assertRaises(ValueError):
            self.store.promote_to_master(self.user,actor_id=self.master,expected_login="other")
        self.store.set_user_active(self.user,False)
        with self.assertRaises(ValueError):
            self.store.promote_to_master(self.user,actor_id=self.master,expected_login="equipe")
        self.assertFalse(self.store.get_public_user(self.user)["is_master"])
        self.assertEqual(self.store.list_audit(action="user_promoted_master")["total"],0)

    def test_concurrent_deactivation_preserves_one_active_master(self):
        other=self.store.create_user("Other","other","Other-test-password!",is_master=True)
        def disable(uid):
            try:return self.store.set_user_active(uid,False)
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(disable,(self.master,other)))
        self.assertEqual(results.count(True),1)
        self.assertEqual(sum(u["is_master"] for u in self.store.list_users(status="active")["users"]),1)
