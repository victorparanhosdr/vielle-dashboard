import json
from pathlib import Path
import tempfile
import unittest

from auth_store import AuthStore
from access_policy import clinic_permissions, project_report


class PermissionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AuthStore(Path(self.temp.name) / "auth.sqlite3")
        self.store.initialize()
        self.master = self.store.create_first_master("Master", "master", "Master-test-password!")

    def test_different_permissions_in_each_clinic_and_revocation(self):
        uid = self.store.create_user("Test", "test", "Temporary-test-password!", clinic_keys=["vielle", "carla"], permissions={"vielle":["financial.view"], "carla":["commercial.view"]}, actor_id=self.master)
        self.assertTrue(self.store.has_permission(uid, "vielle", "financial.view"))
        self.assertFalse(self.store.has_permission(uid, "carla", "financial.view"))
        self.assertFalse(self.store.has_permission(uid, "vielle", "financial.export"))
        token = self.store.login("test", "Temporary-test-password!")
        self.store.update_user(uid, "Test", "test", clinic_keys=["vielle"], permissions={"vielle":[]}, actor_id=self.master)
        self.assertIsNotNone(self.store.session_user(token))
        self.assertFalse(self.store.has_permission(uid, "vielle", "financial.view"))
        self.assertFalse(self.store.user_can_access_clinic(uid, "carla"))

    def test_unsupported_modules_actions_and_missing_view_rejected(self):
        for permissions in ({"carla":["patient_followup.view"]}, {"carla":["master.view"]}, {"carla":["financial.export"]}, {"vielle":["dashboard.view"]}):
            with self.assertRaises(ValueError):
                self.store.create_user("Bad", "bad", "Temporary-test-password!", clinic_keys=["carla"], permissions=permissions)
        self.assertIsNone(self.store.get_user_by_login("bad"))

    def test_master_bypasses_assignments_but_not_module_availability(self):
        self.assertTrue(self.store.has_permission(self.master,"carla","financial.export"))
        self.assertFalse(self.store.has_permission(self.master,"carla","patient_followup.view"))

    def test_migration_preserves_phase5_access_and_is_idempotent(self):
        uid = self.store.create_user("Test", "test", "Temporary-test-password!", clinic_keys=["inspire"])
        with self.store.connection() as conn:
            conn.execute("DROP TABLE user_clinic_permissions")
            conn.execute("DROP TABLE access_profiles")
            conn.execute("DROP TABLE audit_log")
            conn.execute("PRAGMA user_version = 3")
        self.store.initialize();self.store.initialize()
        self.assertEqual(set(self.store.user_permissions(uid,"inspire")),set(clinic_permissions("inspire")))
        self.assertEqual(len(self.store.list_profiles()),5)

    def test_profiles_are_templates_not_privilege_escalation(self):
        before = self.store.list_profiles()
        pid = self.store.save_profile("Leitura", ["financial.view"], actor_id=self.master)
        uid = self.store.create_user("Test", "test", "Temporary-test-password!", clinic_keys=["vielle"], permissions={"vielle":["financial.view"]})
        self.store.save_profile("Leitura", ["commercial.view"], pid, self.master)
        self.assertEqual(self.store.user_permissions(uid,"vielle"),["financial.view"])
        with self.assertRaises(ValueError):self.store.save_profile("Invalid",["master.view"])
        self.assertEqual(len(self.store.list_profiles()),len(before)+1)

    def test_audit_records_admin_changes_and_never_passwords(self):
        password="Temporary-test-password!"
        uid=self.store.create_user("Test","test",password,clinic_keys=["vielle"],permissions={"vielle":["commercial.view"]},actor_id=self.master)
        self.store.login("test","wrong-password")
        self.store.login("test",password)
        self.store.update_user(uid,"Renamed","test",clinic_keys=["carla"],permissions={"carla":["financial.view"]},actor_id=self.master)
        self.store.set_password(uid,"NewTemporary-password!",actor_id=self.master)
        self.store.set_user_active(uid,False,actor_id=self.master)
        self.store.set_user_active(uid,True,actor_id=self.master)
        data=self.store.list_audit();actions={row["action"] for row in data["events"]}
        self.assertTrue({"login","login_denied","user_created","user_updated","clinics_changed","permissions_changed","password_reset","user_deactivated","user_activated"}<=actions)
        self.assertNotIn(password,json.dumps(data));self.assertNotIn("pbkdf2",json.dumps(data))
        for event in data["events"]:
            if event["action"]=="permissions_changed":self.assertEqual(event["actor_name"],"Master")

    def test_report_projection_has_no_sibling_datasets(self):
        report={"connected":True,"filters":{},"pipelines":[],"financial":{"secret":"financial"},"general_panel":{"revenue":1},"paid_traffic":{"secret":"ads"},"whatsapp_audit":{"secret":"messages"},"patient_followup":{"secret":"patients"},"quote_followup":{"secret":"budgets"},"clinica_experts":{"totals":{"secret":1},"booking_registry_users":[]},"totals":{}}
        commercial=project_report(report,"commercial")
        for key in ("financial","general_panel","whatsapp_audit","patient_followup","quote_followup","paid_traffic"):self.assertNotIn(key,commercial)
        dashboard=project_report(report,"dashboard")
        self.assertEqual(dashboard["clinica_experts"],{"booking_registry_users":[]})
        finance=project_report(report,"financial")
        self.assertEqual(set(finance),{"financial","connected","filters","pipelines"})
