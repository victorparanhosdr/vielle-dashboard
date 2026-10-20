import json
from pathlib import Path
import tempfile
import unittest

from auth_store import AuthStore
from crm_access import crm_access, institute_access
from crm_dashboard import dashboard
from crm_forms import FormStore
from crm_store import CrmStore


class UserCrmScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = AuthStore(Path(self.tmp.name) / "auth.sqlite3")
        self.auth.initialize()
        self.user = self.auth.create_user("Equipe", "equipe", "Temporary-test-password!",
                                         clinic_keys=["vielle"], permissions={"vielle": ["crm.view"]})
        self.crm = CrmStore(Path(self.tmp.name) / "crm")
        self.crm.bootstrap({"Marcela": ["Clinica"], "Victor Paranhos de Andrade": ["Permitido", "Restrito"]})
        self.doc = self.crm.listing()["doctor_id"]
        self.other = next(d["id"] for d in self.crm.listing()["doctors"] if d["id"] != self.doc)
        self.allowed, self.hidden = self.crm.listing(self.doc)["pipelines"]
        self.scope = {self.doc: [self.allowed["id"]]}
        self.visible = self.lead(self.allowed, "5500000000002", "Titulo permitido")
        self.blocked = self.lead(self.hidden, "5500000000003", "Titulo secreto")
        self.channel = self.crm.channel({"doctor_id": self.doc, "name": "Atendimento", "phone": "5500000000001"})
        with self.crm.db() as c:
            channel = self.crm.row(c, "channels", self.channel)
        for lead in (self.visible, self.blocked):
            data = self.crm.detail(lead, self.doc)["lead"]
            self.crm.receive(channel["instance"], {"key": {"remoteJid": data["phone"] + "@s.whatsapp.net", "id": lead},
                "message": {"conversation": "Mensagem recebida"}}, "messages.upsert")

    def lead(self, pipeline, phone, title):
        return self.crm.lead({"doctor_id": pipeline["doctor_id"], "pipeline_id": pipeline["id"],
            "stage_id": pipeline["stages"][0]["id"], "name": "Contato sintetico", "phone": phone,
            "title": title}, "Equipe")

    def save(self, value):
        self.auth.update_user(self.user, "Equipe", "equipe", crm_scope=value, actor_id=self.user)

    def test_existing_users_remain_unrestricted_and_scope_is_live_on_existing_session(self):
        token = self.auth.login("equipe", "Temporary-test-password!")
        self.assertIsNone(self.auth.session_user(token)["crm_scope"])
        self.save(self.scope)
        self.assertEqual(self.auth.session_user(token)["crm_scope"], self.scope)
        self.auth.update_user(self.user, "Equipe nova", "equipe", clinic_keys=["vielle"], permissions={"vielle": ["crm.view"]})
        self.assertEqual(self.auth.get_public_user(self.user)["crm_scope"], self.scope)
        self.save({})
        self.assertEqual(self.auth.session_user(token)["crm_scope"], {})
        self.save(None)
        self.assertIsNone(self.auth.session_user(token)["crm_scope"])
        self.assertEqual(len(self.auth.list_audit(action="crm_access_changed")["events"]), 3)

    def test_invalid_scope_rolls_back_identity_and_permissions(self):
        self.save(self.scope)
        for bad in ([], {self.doc: []}, {self.doc: "all"}, {"invalid": None}, {self.doc: [True]}):
            with self.subTest(scope=bad), self.assertRaises(ValueError):
                self.auth.update_user(self.user, "Nao salvar", "outro", crm_scope=bad)
            self.assertEqual(self.auth.get_public_user(self.user)["nome"], "Equipe")
            self.assertEqual(self.auth.get_public_user(self.user)["crm_scope"], self.scope)

    def test_master_cannot_be_scoped(self):
        master = self.auth.create_first_master("Master", "master", "Temporary-test-password!")
        with self.assertRaises(ValueError):
            self.auth.update_user(master, "Master", "master", crm_scope={})
        self.assertIsNone(self.auth.get_public_user(master)["crm_scope"])

    def test_listing_and_kanban_respect_doctor_and_pipeline_selection(self):
        with crm_access(self.scope):
            data = self.crm.listing(kanban_pipeline="")
            self.assertEqual([d["id"] for d in data["doctors"]], [self.doc])
            self.assertEqual([p["id"] for p in data["pipelines"]], [self.allowed["id"]])
            self.assertEqual(data["total_leads"], 1)
            self.assertEqual(data["matched_leads"], 1)
            self.assertEqual([l["id"] for l in data["leads"]], [self.visible])
            with self.assertRaises(PermissionError):
                self.crm.listing(self.other)
            with self.assertRaises(PermissionError):
                self.crm.kanban_page(self.doc, self.hidden["id"], self.hidden["stages"][0]["id"])
            with self.assertRaises(PermissionError):
                self.crm.detail(self.blocked, self.doc)
        with crm_access({self.other: None}):
            self.assertEqual(self.crm.listing()["doctor_id"], self.other)
        with crm_access({}):
            self.assertEqual(self.crm.listing()["doctors"], [])
        self.assertEqual(len(self.crm.listing()["doctors"]), 2)

    def test_write_and_direct_stage_references_cannot_bypass_scope(self):
        original = self.crm.detail(self.visible, self.doc)["lead"]
        with crm_access(self.scope, self.doc):
            with self.assertRaises(PermissionError):
                self.lead(self.hidden, "5500000000004", "Nao criar")
            with self.assertRaises(PermissionError):
                self.crm.lead({**original, "pipeline_id": self.hidden["id"], "stage_id": self.hidden["stages"][0]["id"]}, "Equipe")
            with self.crm.db() as c, self.assertRaises(PermissionError):
                self.crm.row(c, "stages", self.hidden["stages"][0]["id"])
        self.assertEqual(self.crm.detail(self.visible, self.doc)["lead"]["pipeline_id"], self.allowed["id"])

    def test_inbox_search_metrics_and_files_do_not_include_hidden_contacts(self):
        blocked = self.crm.detail(self.blocked, self.doc)["lead"]
        visible = self.crm.detail(self.visible, self.doc)["lead"]
        with crm_access(self.scope, self.doc):
            inbox = self.crm.inbox(self.doc, self.user)
            self.assertEqual(inbox["total"], 1)
            self.assertEqual(inbox["conversations"][0]["lead_id"], self.visible)
            metrics = dashboard(self.crm, self.doc)
            self.assertEqual(metrics["pending_clients"], 1)
            self.assertEqual(metrics["received_messages"], 1)
            self.assertEqual([f["id"] for f in metrics["funnels"]], [self.allowed["id"]])
            self.assertTrue(self.crm.conversation(self.doc, self.channel, visible["contact_id"])["messages"])
            for action in (lambda: self.crm.conversation(self.doc, self.channel, blocked["contact_id"]),
                           lambda: self.crm.conversation_search(self.doc, self.channel, blocked["contact_id"], "Mensagem")):
                with self.assertRaises(PermissionError):
                    action()
            with self.crm.db() as c:
                with self.assertRaises(PermissionError):
                    from crm_access import check_row
                    check_row(c, "message_media", {"doctor_id": self.doc, "contact_id": blocked["contact_id"]})
                with self.assertRaises(PermissionError):
                    check_row(c, "voice_calls", {"doctor_id": self.doc, "contact_id": visible["contact_id"], "lead_id": self.blocked})

    def test_shared_contact_keeps_conversation_but_hides_other_opportunity_title(self):
        shared = self.lead(self.hidden, "5500000000002", "Nao revelar este titulo")
        contact = self.crm.detail(shared, self.doc)["lead"]["contact_id"]
        with crm_access(self.scope, self.doc):
            opportunities = self.crm.contact_opportunities(self.doc, contact)["opportunities"]
            self.assertEqual([o["id"] for o in opportunities], [self.visible])
            item = self.crm.inbox(self.doc, self.user)["conversations"][0]
            self.assertEqual(item["title"], "Titulo permitido")
            self.assertEqual(item["lead_id"], self.visible)

    def test_clinic_wide_history_is_denied_for_restricted_access(self):
        with crm_access(self.scope, self.doc):
            with self.assertRaises(PermissionError):
                self.crm.commercial(self.visible, self.doc, "clinic")
            with self.assertRaises(PermissionError):
                self.crm.appointments(self.visible, self.doc, "clinic")

    def test_call_history_does_not_leak_other_funnel_of_same_contact(self):
        from crm_voice import VoiceService
        service = VoiceService(self.crm)
        shared = self.lead(self.hidden, "5500000000002", "Outro funil")
        contact = self.crm.detail(shared, self.doc)["lead"]["contact_id"]
        with self.crm.db() as c:
            for index, lead in enumerate((self.visible, shared)):
                values = {"id": f"call-{index}", "request_id": f"request-{index}", "lead_id": lead,
                    "contact_id": contact, "doctor_id": self.doc, "patient_uuid": "", "customer_name": "Teste",
                    "destination": "+5511999999999", "line_id": "test", "caller_number": "+551130000000",
                    "line_name": "Teste", "actor_id": "test", "actor_name": "Teste", "account_sid": "test",
                    "created_at": 1, "updated_at": 1, "notice": "", "max_seconds": 60, "status": "completed"}
                columns = ",".join(values)
                c.execute(f"INSERT INTO voice_calls({columns}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
        with crm_access(self.scope, self.doc):
            result = service.history(self.visible, self.doc)
            self.assertEqual(result["total"], 1)
            self.assertEqual([call["id"] for call in result["calls"]], ["call-0"])

    def test_frozen_campaign_audience_is_rechecked_after_scope_changes(self):
        with self.crm.db() as c:
            c.execute("INSERT INTO campaign_audiences VALUES(?,?,?,?,?,?,?)", ("test-preview", self.doc, str(self.user),
                json.dumps({"ids": [self.visible, self.blocked]}), "test", 9999999999, ""))
        with crm_access(self.scope, self.doc), self.crm.db() as c, self.assertRaises(PermissionError):
            self.crm.row(c, "campaign_audiences", "test-preview")

    def test_xp_totals_do_not_count_hidden_pipeline(self):
        for lead in (self.visible, self.blocked):
            self.crm.activity({"id": lead, "doctor_id": self.doc, "kind": "followup", "body": "Atividade"}, "Equipe")
        full = self.crm.listing(self.doc)["game"]["xp"]
        with crm_access(self.scope, self.doc):
            data = self.crm.listing(self.doc)
            self.assertEqual(data["game"]["xp"], full // 2)
            self.assertEqual(data["game"]["followups"], 1)

    def test_form_answers_follow_both_published_and_draft_pipeline(self):
        forms = FormStore(self.crm)
        definition = {"title": "Formulario", "fields": [{"id": "name", "type": "name", "label": "Nome"}]}
        unbound = forms.save({"doctor_id": self.doc, "definition": definition}, self.user)
        binding = {"pipeline_id": self.allowed["id"], "stage_id": self.allowed["stages"][0]["id"]}
        linked = forms.save({"doctor_id": self.doc, "definition": {**definition, "crm": binding}}, self.user)
        forms.publish(linked["id"], self.doc, linked["revision"], "published")
        with crm_access(self.scope, self.doc):
            self.assertEqual([f["id"] for f in forms.listing(self.doc)], [linked["id"]])
            with self.assertRaises(PermissionError):
                forms.detail(unbound["id"], self.doc)
            with self.assertRaises(PermissionError):
                forms.save({"doctor_id": self.doc, "definition": definition}, self.user)
        with self.crm.db() as c:
            row = forms.owned(c, linked["id"], self.doc)
            draft = json.loads(row["draft"])
            draft["crm"]["pipeline_id"] = self.hidden["id"]
            c.execute("UPDATE crm_forms SET draft=? WHERE id=?", (json.dumps(draft), linked["id"]))
        with crm_access(self.scope, self.doc), self.assertRaises(PermissionError):
            forms.detail(linked["id"], self.doc)

    def test_institute_permission_remains_an_additional_requirement(self):
        with self.crm.db() as c:
            c.execute("UPDATE pipelines SET institute_key='victor-paranhos',course_key='regencode' WHERE id=?", (self.allowed["id"],))
        with crm_access(self.scope), institute_access([]):
            self.assertEqual(self.crm.listing()["pipelines"], [])
            with self.assertRaises(PermissionError):
                self.crm.detail(self.visible, self.doc)

