from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from auth_store import AuthStore
from team_presence import TeamPresence, TZ


class TeamProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.passwords = patch("auth_store.PASSWORD_ITERATIONS", 100_000)
        self.passwords.start(); self.addCleanup(self.passwords.stop)
        self.auth = AuthStore(Path(self.tmp.name) / "auth.sqlite3")
        self.auth.initialize()
        self.master = self.auth.create_first_master("Master Exemplo", "master", "Temporary-test-password!")
        self.staff = self.auth.create_user("Equipe Exemplo", "equipe", "Temporary-test-password!",
            clinic_keys=["vielle", "inspire"], permissions={"vielle": ["crm.view"], "inspire": []})
        self.team = TeamPresence(self.auth)
        self.now = datetime(2026, 10, 22, 9, tzinfo=TZ).timestamp()

    def change(self, profiles, **values):
        return self.auth.update_user(self.staff, "Equipe Exemplo", "equipe", team_profiles=profiles,
                                     actor_id=self.master, **values)

    def ids(self, clinic="vielle", viewer=None):
        return {p["id"] for p in self.team.presence(clinic, viewer or self.master, now=self.now)["members"]}

    def test_defaults_preserve_staff_and_hide_master_without_inferring_permissions(self):
        self.assertEqual(self.auth.get_public_user(self.staff)["team_profiles"], {"vielle": "attendant", "inspire": "attendant"})
        self.assertEqual(self.auth.get_public_user(self.master)["team_profiles"]["vielle"], "management")
        self.assertEqual(self.ids(), {self.staff})
        self.auth.update_user(self.staff, "Equipe Exemplo", "equipe", permissions={"vielle": ["team.view"], "inspire": []})
        self.assertEqual(self.ids(), {self.staff})

    def test_same_person_can_have_different_profiles_per_clinic(self):
        before = self.auth.get_public_user(self.staff)["permissions"]
        self.change({"vielle": "management"})
        self.assertEqual(self.ids(), set())
        self.assertEqual(self.ids("inspire"), {self.staff})
        self.assertEqual(self.auth.get_public_user(self.staff)["permissions"], before)
        self.assertFalse(self.auth.has_permission(self.staff, "vielle", "team.view"))
        self.change({"vielle": "attendant"})
        self.assertEqual(self.ids(), {self.staff})

    def test_master_can_choose_to_appear_without_changing_master_access(self):
        before = self.auth.get_public_user(self.master)
        self.auth.update_user(self.master, before["nome"], before["login"], team_profiles={"vielle": "attendant"}, actor_id=self.master)
        after = self.auth.get_public_user(self.master)
        self.assertIn(self.master, self.ids())
        self.assertEqual(after["is_master"], before["is_master"])
        self.assertEqual(after["permissions"], before["permissions"])
        self.assertEqual(after["team_profiles"]["inspire"], "management")

    def test_legacy_edits_and_permission_replacement_preserve_profiles(self):
        self.change({"vielle": "management"})
        self.auth.update_user(self.staff, "Novo Nome", "equipe", clinic_keys=["inspire", "vielle"],
                              permissions={"vielle": ["crm.view"], "inspire": []})
        self.assertEqual(self.auth.get_public_user(self.staff)["team_profiles"], {"vielle": "management", "inspire": "attendant"})

    def test_membership_removal_prunes_profile_not_history(self):
        self.change({"vielle": "management"})
        self.auth.update_user(self.staff, "Equipe Exemplo", "equipe", clinic_keys=["inspire"])
        self.assertEqual(self.auth.get_public_user(self.staff)["team_profiles"], {"inspire": "attendant"})
        with self.auth.connection() as c:
            self.assertFalse(c.execute("SELECT 1 FROM user_team_profiles WHERE user_id=? AND clinic_key='vielle'", (self.staff,)).fetchone())
        self.auth.update_user(self.staff, "Equipe Exemplo", "equipe", clinic_keys=["vielle", "inspire"])
        self.assertEqual(self.auth.get_public_user(self.staff)["team_profiles"]["vielle"], "attendant")

    def test_invalid_or_unassigned_profiles_roll_back_all_user_changes(self):
        before = self.auth.get_public_user(self.staff)
        for value in (None, [], "management", {"vielle": "master"}, {"carla": "management"},
                      {"vielle": True}, {"vielle": []}, {"unknown": "attendant"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.auth.update_user(self.staff, "Must Not Change", "newlogin", team_profiles=value,
                                      permissions={"vielle": [], "inspire": []})
            self.assertEqual(self.auth.get_public_user(self.staff), before)

    def test_create_validation_is_atomic_and_audit_has_before_and_after(self):
        count = self.auth.list_users()["total"]
        with self.assertRaises(ValueError):
            self.auth.create_user("Invalid", "invalid", "Temporary-test-password!", clinic_keys=["vielle"],
                                  team_profiles={"carla": "management"})
        self.assertEqual(self.auth.list_users()["total"], count)
        self.change({"vielle": "management"})
        event = self.auth.list_audit(action="team_profiles_changed")["events"][0]
        self.assertEqual(event["actor_id"], self.master)
        self.assertEqual(event["details"]["before"]["vielle"], "attendant")
        self.assertEqual(event["details"]["after"]["vielle"], "management")

    def test_role_change_does_not_revoke_session_or_erase_recorded_time(self):
        with patch("time.time", return_value=self.now):
            token = self.auth.login("equipe", "Temporary-test-password!")
        self.team.heartbeat("vielle", self.staff, token,
            {"client_id": uuid.uuid4().hex, "sequence": 1, "activity_age": 0}, now=self.now)
        self.change({"vielle": "management"})
        with patch("time.time", return_value=self.now+30):
            self.assertIsNotNone(self.auth.session_user(token))
        self.assertNotIn(self.staff, self.ids())
        report = self.team.report("vielle", {"date": ["2026-10-22"]}, now=self.now+60)
        self.assertEqual(next(p["logged_seconds"] for p in report["members"] if p["id"] == self.staff), 60)

    def test_old_database_reinitialization_keeps_users_and_existing_access(self):
        before = self.auth.get_public_user(self.staff)
        with self.auth.connection() as c:
            c.execute("DROP TABLE user_team_profiles")
        self.auth.initialize(); self.auth.initialize()
        self.assertEqual(self.auth.get_public_user(self.staff), before)
        self.assertEqual(self.ids(), {self.staff})

    def test_hidden_manager_can_request_help_but_cannot_be_a_new_recipient(self):
        with patch("time.time", return_value=self.now):
            token = self.auth.login("equipe", "Temporary-test-password!")
        self.team.heartbeat("vielle", self.staff, token,
            {"client_id": uuid.uuid4().hex, "sequence": 1, "activity_age": 0}, now=self.now)
        request = self.team.help("vielle", self.master,
            {"recipient_id": self.staff, "message": "Ajuda de teste", "request_id": uuid.uuid4().hex}, now=self.now+10)
        data = self.team.presence("vielle", self.staff, now=self.now+11)
        self.assertNotIn(self.master, {p["id"] for p in data["members"]})
        self.assertEqual(data["help_requests"][0]["id"], request)
        self.team.help_action("vielle", self.staff, {"id": request, "action": "accept"}, now=self.now+12)
        self.change({"vielle": "management"})
        self.team.help_action("vielle", self.staff, {"id": request, "action": "complete"}, now=self.now+13)
        with self.assertRaises(PermissionError):
            self.team.help("vielle", self.master,
                {"recipient_id": self.staff, "message": "Novo pedido", "request_id": uuid.uuid4().hex}, now=self.now+14)
