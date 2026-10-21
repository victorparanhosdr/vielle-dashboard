from datetime import datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
import uuid

from auth_store import AuthStore
from crm_access import crm_access, institute_access
from crm_engagement import snapshot
from crm_reminders import assignees, lead_choices, listing, local_due, save
from crm_store import CrmStore, Conflict, TZ


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = AuthStore(Path(self.tmp.name) / "auth.sqlite3")
        self.auth.initialize()
        self.uid = self.auth.create_first_master("Gestor", "master", "Temporary-test-password!")
        self.staff_id = self.auth.create_user("Equipe", "equipe", "Temporary-test-password!", clinic_keys=["vielle"], permissions={"vielle": ["crm.view"]})
        self.user = self.auth.get_public_user(self.uid)
        self.crm = CrmStore(Path(self.tmp.name) / "crm")
        self.crm.bootstrap({"Doutor A": ["Clinica", "Curso"], "Doutor B": ["Outro"]})
        data = self.crm.listing()
        self.doc = data["doctor_id"]
        self.pipeline, self.other_pipeline = data["pipelines"]
        self.lead = self.make_lead(self.pipeline, "5500000000001")
        self.other = self.make_lead(self.other_pipeline, "5500000000002")
        self.now = datetime(2026, 10, 21, 10, 0, tzinfo=TZ).timestamp()
        self.time_patch = patch("time.time", return_value=self.now)
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)

    def make_lead(self, pipeline, phone):
        return self.crm.lead({"doctor_id": pipeline["doctor_id"], "pipeline_id": pipeline["id"], "stage_id": pipeline["stages"][0]["id"],
            "name": "Cliente Exemplo", "phone": phone, "title": "Solicitou retorno"}, "Equipe")

    def create(self, **changes):
        payload = {"lead_id": self.lead, "title": "Chamar amanha", "note": "Solicitado pelo cliente", "due_local": "2026-10-22T09:00",
                   "assignee_id": str(self.uid), "request_id": str(uuid.uuid4()), **changes}
        return save(self.crm, self.auth, self.user, payload)

    def update(self, item, action, **changes):
        return save(self.crm, self.auth, self.user, {"id": item["id"], "revision": item["revision"], **changes}, action)

    def test_lifecycle_audit_persistence_and_no_extra_lead_or_message(self):
        item = self.create()
        self.assertEqual(item["due_at"], datetime(2026, 10, 22, 9, tzinfo=TZ).timestamp())
        self.assertFalse(item["overdue"])
        self.assertEqual(len(listing(self.crm, self.user, {})["items"]), 1)
        done = self.update(item, "done")
        self.assertEqual(done["status"], "done")
        reopened = self.update(done, "reopen")
        cancelled = self.update(reopened, "cancel")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(listing(self.crm, self.user, {})["total"], 0)
        reopened_store = CrmStore(self.crm.path.parent)
        self.assertEqual(listing(reopened_store, self.user, {"status": ["cancelled"]})["total"], 1)
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM leads").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='reminder'").fetchone()[0], 4)

    def test_due_alert_acknowledgement_and_reschedule_reset(self):
        item = self.create(due_local="2026-10-21T09:00")
        self.assertTrue(item["overdue"])
        self.assertEqual(listing(self.crm, self.user, {}, alerts=True)["total"], 1)
        ack = self.update(item, "ack")
        self.assertEqual(listing(self.crm, self.user, {}, alerts=True)["total"], 0)
        self.assertEqual(listing(self.crm, self.user, {"status": ["overdue"]})["total"], 1)
        edited = self.update(ack, "save", title=item["title"], due_local="2026-10-21T09:30", assignee_id=self.uid)
        self.assertEqual(edited["acknowledged_at"], 0)
        self.assertEqual(listing(self.crm, self.user, {}, alerts=True)["total"], 1)
        self.update(edited, "done")
        self.assertEqual(listing(self.crm, self.user, {}, alerts=True)["total"], 0)

    def test_request_idempotence_and_revision_conflicts(self):
        request = str(uuid.uuid4())
        first = self.create(request_id=request)
        second = self.create(request_id=request)
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(Conflict):
            self.create(request_id=request, title="Outra tarefa")
        self.update(first, "done")
        with self.assertRaises(Conflict):
            self.update(second, "cancel")
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM crm_reminders").fetchone()[0], 1)

    def test_scope_filters_reads_alerts_picker_and_mutations(self):
        visible = self.create(due_local="2026-10-21T09:00", assignee_id=self.staff_id)
        hidden = self.create(lead_id=self.other, due_local="2026-10-21T09:00", assignee_id=self.staff_id)
        scope = {self.doc: [self.pipeline["id"]]}
        staff = self.auth.get_public_user(self.staff_id)
        with crm_access(scope), institute_access([]):
            self.assertEqual([i["id"] for i in listing(self.crm, staff, {}, alerts=True)["items"]], [visible["id"]])
            self.assertEqual([l["id"] for l in lead_choices(self.crm, {})], [self.lead])
            with self.assertRaises(PermissionError):
                save(self.crm, self.auth, staff, {"id": hidden["id"], "revision": 1}, "done")
        with crm_access({}), institute_access([]):
            self.assertEqual(listing(self.crm, staff, {}, alerts=True)["total"], 0)
        with self.crm.db() as c:
            c.execute("UPDATE leads SET deleted_at=? WHERE id=?", (self.now, self.lead))
        with crm_access(scope):
            self.assertEqual(listing(self.crm, staff, {}, alerts=True)["total"], 0)
            with self.assertRaises(LookupError):
                self.update(visible, "done")

    def test_assignee_must_have_live_permission_and_ack_is_personal(self):
        self.auth.update_user(self.staff_id, "Equipe", "equipe", crm_scope={self.doc: [self.other_pipeline["id"]]})
        with self.assertRaises(ValueError):
            self.create(assignee_id=self.staff_id)
        with self.crm.db() as c:
            lead = self.crm.row(c, "leads", self.lead)
        self.assertNotIn(str(self.staff_id), [u["id"] for u in assignees(self.crm, self.auth, lead)])
        item = self.create()
        with self.assertRaises(PermissionError):
            save(self.crm, self.auth, self.auth.get_public_user(self.staff_id), {"id": item["id"], "revision": 1}, "ack")

    def test_invalid_dates_filters_and_paging(self):
        for value in ("2026-02-30T09:00", "2026-10-22", "2026-10-22T09:00Z", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_due(value)
        for query in ({"status": ["invalid"]}, {"page": ["-1"]}, {"owner": ["who"]}):
            with self.assertRaises(ValueError):
                listing(self.crm, self.user, query)
        self.create(title="100% retorno")
        self.assertEqual(listing(self.crm, self.user, {"search": ["%"]})["total"], 1)
        self.assertEqual(listing(self.crm, self.user, {"search": ["_secret"]})["total"], 0)

    def test_engagement_real_points_fresh_messages_and_access(self):
        self.crm.activity({"id": self.lead, "doctor_id": self.doc, "kind": "followup", "body": "Retorno realizado"}, "Gestor")
        self.crm.activity({"id": self.other, "doctor_id": self.doc, "kind": "followup", "body": "Outro retorno"}, "Oculto")
        channel = self.crm.channel({"doctor_id": self.doc, "name": "Organico", "phone": "5500000000003"})
        with self.crm.db() as c:
            contact = self.crm.row(c, "leads", self.lead)["contact_id"]
            for id_, direction, age in (("new", "in", 1), ("old", "in", 600), ("sent", "out", 1)):
                c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at) VALUES(?,?,?,?,?,?,?)", (id_, channel, contact, "Teste", direction, "received", self.now-age))
        with crm_access({self.doc: [self.pipeline["id"]]}), institute_access([]):
            baseline = snapshot(self.crm, self.user, {})
            self.assertEqual(baseline["fresh_messages"], 0)
            self.assertEqual(baseline["celebrations"], [])
            data = snapshot(self.crm, self.user, {"watch": ["1"]})
            self.assertEqual(data["fresh_messages"], 1)
            self.assertEqual(data["journey"]["xp"], 3)
            self.assertEqual(data["journey"]["team"], [{"name": "Gestor", "xp": 3}])
            self.assertEqual(len(data["celebrations"]), 1)
            self.assertEqual(snapshot(self.crm, self.user, {"watch": ["1"], "messages_after": [str(data["message_cursor"])], "xp_after": [str(data["xp_cursor"])]})["fresh_messages"], 0)

    def test_routes_require_crm_scope_and_same_origin(self):
        import crm_api
        payload = {"lead_id": self.lead, "title": "Ligar", "due_local": "2026-10-22T09:00", "request_id": str(uuid.uuid4())}
        body = json.dumps(payload).encode()
        handler = SimpleNamespace(command="POST", current_user=self.user, server=SimpleNamespace(auth_store=self.auth),
            connection=SimpleNamespace(settimeout=lambda _: None),
            headers={"Content-Length": str(len(body))}, rfile=BytesIO(body), same_origin_request=lambda: False,
            auth_json=lambda data, status=200: (status, data))
        with patch.object(crm_api, "store_for", return_value=self.crm):
            path = urlparse("/api/crm/reminders/save?clinic=vielle")
            self.assertEqual(crm_api.handle_request(handler, path)[0], 403)
            handler.same_origin_request = lambda: True
            status, data = crm_api.handle_request(handler, path)
            self.assertEqual(status, 200, data)
            self.assertTrue(data["ok"])
            handler.command = "GET"
            status, data = crm_api.handle_request(handler, urlparse("/api/crm/reminders?clinic=vielle"))
            self.assertEqual(data["total"], 1)
            self.assertEqual(crm_api.handle_request(handler, urlparse("/api/crm/engagement?clinic=other"))[0], 403)
