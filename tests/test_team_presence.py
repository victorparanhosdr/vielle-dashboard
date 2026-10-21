from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from auth_store import AuthStore
from team_presence import TeamPresence, IDLE_SECONDS, LEASE_SECONDS, TZ


class TeamPresenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = AuthStore(Path(self.tmp.name) / "auth.sqlite3")
        self.auth.initialize()
        self.start = datetime(2026, 10, 21, 9, tzinfo=TZ).timestamp()
        self.patch = patch("time.time", return_value=self.start)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.uid = self.auth.create_user("Ana", "ana", "Temporary-test-password!", clinic_keys=["vielle", "inspire"], permissions={"vielle":["crm.view"],"inspire":["tasks.view"]})
        self.other = self.auth.create_user("Bruno", "bruno", "Temporary-test-password!", clinic_keys=["vielle"], permissions={"vielle":["crm.view"]})
        self.outside = self.auth.create_user("Outra clínica", "other", "Temporary-test-password!", clinic_keys=["carla"], permissions={"carla":[]})
        self.token = self.auth.login("ana", "Temporary-test-password!")
        self.other_token = self.auth.login("bruno", "Temporary-test-password!")
        self.store = TeamPresence(self.auth)
        self.client = uuid.uuid4().hex

    def beat(self, offset, *, client=None, sequence=None, age=None, clinic="vielle", leave=False, uid=None, token=None):
        return self.store.heartbeat(clinic, uid or self.uid, token or self.token,
            {"client_id":client or self.client,"sequence":sequence if sequence is not None else int(offset)+1,
             "activity_age":offset if age is None else age}, leave=leave, now=self.start+offset)

    def report(self, offset, clinic="vielle", **query):
        return self.store.report(clinic, {"date":["2026-10-21"], **query}, now=self.start+offset)

    def person(self, offset, uid=None):
        return next(p for p in self.report(offset)["members"] if p["id"] == (uid or self.uid))

    def test_active_idle_and_totals_are_not_in_public_presence(self):
        for n in range(0, 601, 30):
            self.beat(n)
        p = self.person(600)
        self.assertEqual((p["active_seconds"],p["idle_seconds"],p["logged_seconds"]), (300,300,600))
        public = self.store.presence("vielle", self.uid, now=self.start+600)
        self.assertEqual(public["members"][0]["status"], "idle")
        self.assertEqual(set(public["members"][0]), {"id","name","status","status_since"})
        self.assertNotIn("login", json.dumps(public))
        self.assertNotIn(self.outside, [p["id"] for p in public["members"]])

    def test_two_tabs_and_two_devices_do_not_double_count(self):
        tab = uuid.uuid4().hex
        second = self.auth.login("ana", "Temporary-test-password!")
        for n in range(0,301,30):
            self.beat(n,age=0)
            self.beat(n,client=tab,token=second,age=0)
        self.assertEqual(self.person(300)["logged_seconds"],300)
        self.beat(310,leave=True)
        self.assertEqual(self.person(315)["status"],"active")
        self.assertEqual(self.person(315)["logged_seconds"],315)

    def test_lunch_logout_leaves_a_gap_and_no_pause_status(self):
        for n in (0,30,60): self.beat(n,age=0)
        with patch("time.time",return_value=self.start+70): self.auth.logout(self.token)
        self.assertEqual(self.person(3700)["logged_seconds"],70)
        with patch("time.time",return_value=self.start+3700): token=self.auth.login("ana","Temporary-test-password!")
        self.beat(3700,token=token,client=uuid.uuid4().hex,age=0)
        self.assertEqual(self.person(3730)["logged_seconds"],100)
        self.assertEqual([e["kind"] for e in self.person(3730)["sessions"]],["login","logout","login"])
        self.assertEqual({i["status"] for i in self.person(3730)["intervals"]},{"active","offline"})

    def test_missing_heartbeat_and_session_expiry_close_at_the_right_time(self):
        self.beat(0)
        p=self.person(1000)
        self.assertEqual(p["logged_seconds"],LEASE_SECONDS)
        self.assertEqual(p["status_since"],self.start+LEASE_SECONDS)
        self.beat(1100,age=0)
        with self.auth.connection() as c:
            c.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?",(self.start+1140,self.auth.token_hash(self.token)))
        self.beat(1120,age=0)
        self.assertEqual(self.person(1500)["logged_seconds"],LEASE_SECONDS+40)

    def test_late_and_duplicate_requests_cannot_reopen_a_closed_tab(self):
        self.beat(0,sequence=1)
        self.beat(20,sequence=3,leave=True)
        self.beat(25,sequence=2,age=0)
        self.assertEqual(self.person(50)["logged_seconds"],20)
        fresh=uuid.uuid4().hex
        self.beat(100,client=fresh,sequence=5,leave=True)
        self.beat(110,client=fresh,sequence=4,age=0)
        self.assertEqual(self.person(120)["status"],"offline")

    def test_explicit_activity_ends_idle_without_rewriting_the_past(self):
        for n in range(0,361,30): self.beat(n)
        self.beat(390,age=10)
        p=self.person(400)
        self.assertEqual((p["active_seconds"],p["idle_seconds"]),(320,80))
        self.assertEqual(p["status_since"],self.start+380)

    def test_disabling_password_reset_and_membership_changes_close_presence(self):
        self.beat(0)
        with patch("time.time",return_value=self.start+20): self.auth.set_password(self.uid,"Different-test-password!")
        self.assertEqual(self.person(200)["logged_seconds"],20)
        with self.assertRaises(PermissionError): self.beat(201)
        with patch("time.time",return_value=self.start+300): token=self.auth.login("ana","Different-test-password!")
        self.beat(300,token=token,age=0)
        with patch("time.time",return_value=self.start+320): self.auth.set_user_active(self.uid,False)
        public=self.store.presence("vielle",self.other,now=self.start+330)
        self.assertNotIn(self.uid,[p["id"] for p in public["members"]])
        self.assertEqual(self.person(330)["logged_seconds"],40)

    def test_clinics_are_separate_and_heartbeat_cannot_spoof_identity(self):
        self.beat(0)
        self.beat(30,clinic="inspire")
        self.assertEqual(next(p for p in self.report(60,"inspire")["members"] if p["id"]==self.uid)["logged_seconds"],30)
        with self.assertRaises(PermissionError): self.beat(30,clinic="carla")
        with self.assertRaises(PermissionError): self.beat(30,uid=self.other)
        for key,value in (("activity_age",float("nan")),("activity_age",-1),("client_id","bad"),("sequence",True)):
            with self.assertRaises(ValueError): self.store.heartbeat("vielle",self.uid,self.token,{"client_id":self.client,"sequence":3,"activity_age":0,key:value},now=self.start+30)

    def test_help_is_idempotent_personal_scoped_and_has_lifecycle(self):
        self.beat(0,uid=self.other,token=self.other_token,age=0)
        payload={"recipient_id":self.other,"message":"Pode me ajudar?","request_id":uuid.uuid4().hex}
        id_=self.store.help("vielle",self.uid,payload,now=self.start+10)
        self.assertEqual(id_,self.store.help("vielle",self.uid,payload,now=self.start+11))
        with self.assertRaises(ValueError): self.store.help("vielle",self.uid,{**payload,"message":"Outra mensagem"},now=self.start+12)
        with self.assertRaises(PermissionError): self.store.help_action("vielle",self.uid,{"id":id_,"action":"accept"},now=self.start+12)
        with self.assertRaises(LookupError): self.store.help_action("inspire",self.other,{"id":id_,"action":"accept"},now=self.start+12)
        self.store.help_action("vielle",self.other,{"id":id_,"action":"accept"},now=self.start+12)
        self.assertEqual(self.store.presence("vielle",self.uid,now=self.start+13)["help_requests"][0]["status"],"accepted")
        self.store.help_action("vielle",self.uid,{"id":id_,"action":"complete"},now=self.start+14)
        with self.assertRaises(ValueError): self.store.help("vielle",self.uid,{**payload,"recipient_id":self.uid},now=self.start+15)
        with self.assertRaises(PermissionError): self.store.help("vielle",self.uid,{**payload,"recipient_id":self.outside},now=self.start+15)

    def test_help_from_offline_members_is_rejected_and_not_shared_with_other_users(self):
        with self.assertRaises(ValueError): self.store.help("vielle",self.uid,{"recipient_id":self.other,"message":"Ajuda","request_id":uuid.uuid4().hex},now=self.start)
        self.assertEqual(self.store.presence("vielle",self.uid,now=self.start)["help_requests"],[])

    def test_report_validates_ranges_and_new_users_do_not_have_invented_history(self):
        data=self.report(0)
        self.assertEqual(data["totals"]["logged_seconds"],0)
        self.assertIsNone(data["first_record_at"])
        self.assertTrue(all(p["status_since"] is None for p in data["members"]))
        for query in ({"days":["500"]},{"date":["bad"]},{"date":["2026-10-22"]},{"user":[str(self.outside)]}):
            with self.assertRaises((ValueError,LookupError)): self.report(0,**query)


if __name__ == "__main__": unittest.main()
