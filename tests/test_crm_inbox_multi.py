import unittest
from unittest.mock import patch

from crm_access import CRM_SCOPE, CURRENT_DOCTOR, crm_access, institute_access
from crm_inbox import combined
from tests import test_crm as fixture


class MultiInboxTests(unittest.TestCase):
    def setUp(self):
        fixture.CrmTests.setUp(self)
        self.second = self.store.channel({"doctor_id": self.b, "name": "Trafego Pago", "phone": "5500000000004"})

    def incoming(self, channel, phone, provider):
        with self.store.db() as c:
            ch = self.store.row(c, "channels", channel)
        self.store.receive(ch["instance"], {"key": {"id": provider, "remoteJid": phone + "@s.whatsapp.net"},
            "pushName": "Contato sintetico", "message": {"conversation": "Quero agendar"}}, "messages.upsert")
        return self.store.inbox(ch["doctor_id"], "user")["conversations"][0]

    def test_combines_without_merging_same_contact_on_different_numbers(self):
        first = self.incoming(self.channel, "5500000000002", "first")
        second = self.incoming(self.second, "5500000000002", "second")
        result = combined(self.store, [self.a, self.b, self.a], "user")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["counts"], {"all": 2, "unread": 2, "waiting": 2})
        self.assertEqual([r["doctor_id"] for r in result["conversations"]], [self.b, self.a])
        self.assertEqual([r["channel_name"] for r in result["conversations"]], ["Trafego Pago", "Atendimento"])
        self.assertEqual({r["contact_id"] for r in result["conversations"]}, {first["contact_id"]})
        self.assertEqual({r["doctor_name"] for r in result["conversations"]}, {"Doutor A", "Doutor B"})
        self.assertEqual(len(result["doctors"]), 2)
        self.assertEqual(len(result["channels"]), 2)
        self.assertTrue(all(p["stages"] for p in result["pipelines"]))
        self.assertNotIn("instance", result["channels"][0])
        self.store.close_conversation({"doctor_id": self.a, "channel_id": self.channel,
            "contact_id": first["contact_id"], "cursor": first["seq"]}, "user")
        result = combined(self.store, [self.a, self.b], "user", kind="waiting")
        self.assertEqual(result["counts"], {"all": 2, "unread": 1, "waiting": 1})
        self.assertEqual(result["conversations"][0]["channel_id"], self.second)
        self.assertEqual(self.store.conversation(self.b, self.second, second["contact_id"])["unread"], 1)

    def test_filters_and_waiting_order_apply_across_owners(self):
        self.incoming(self.channel, "5500000000002", "first")
        self.incoming(self.second, "5500000000003", "second")
        result = combined(self.store, [self.b, self.a], "user", kind="waiting")
        self.assertEqual([r["doctor_id"] for r in result["conversations"]], [self.a, self.b])
        self.assertEqual(combined(self.store, [self.a, self.b], "user", search="nao existe")["total"], 0)
        result = combined(self.store, [self.a, self.b], "user", channel=self.second)
        self.assertEqual(result["counts"]["all"], 1)
        self.assertEqual(result["conversations"][0]["doctor_id"], self.b)
        with self.assertRaises(ValueError):
            combined(self.store, [self.a], "user", channel=self.second)

    def test_financial_values_use_each_doctor_and_global_sort(self):
        self.incoming(self.channel, "5500000000002", "first")
        self.incoming(self.second, "5500000000003", "second")

        def attach(c, items, doctor, search):
            for item in items:
                item["customer_history"] = {"sales_state": "ready", "sales_cents": 900 if doctor == "Doutor A" else 100,
                                            "sales_count": 1, "quotes_state": "ready"}
        with patch.object(self.store, "attach_customer_values", side_effect=attach):
            result = combined(self.store, [self.b, self.a], "user", filters={"sort": "spent"})
            self.assertEqual([r["doctor_id"] for r in result["conversations"]], [self.a, self.b])
            result = combined(self.store, [self.b, self.a], "user", filters={"min_spent_cents": 500})
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["conversations"][0]["doctor_id"], self.a)

    def test_doctor_funnel_and_institute_permissions_are_not_broadened(self):
        self.incoming(self.channel, "5500000000002", "allowed")
        self.incoming(self.second, "5500000000003", "other")
        with self.store.db() as c:
            first_pipeline = c.execute("SELECT pipeline_id FROM leads WHERE doctor_id=?", (self.a,)).fetchone()[0]
        scope = {self.a: [first_pipeline], self.b: None}
        with crm_access(scope, self.a):
            self.assertEqual(combined(self.store, [self.a, self.b], "user")["total"], 2)
            self.assertEqual(CURRENT_DOCTOR.get(), self.a)
            self.assertEqual(CRM_SCOPE.get(), scope)
            visible = combined(self.store, [self.a], "user")
            self.assertEqual([p["id"] for p in visible["pipelines"]], [first_pipeline])
        with crm_access({self.a: None}, self.a):
            with self.assertRaises(PermissionError):
                combined(self.store, [self.a, self.b], "user")
            self.assertEqual(CURRENT_DOCTOR.get(), self.a)
        with self.store.db() as c:
            c.execute("UPDATE pipelines SET institute_key='private-institute' WHERE id=?", (first_pipeline,))
        with institute_access([]), crm_access(scope, self.a):
            result = combined(self.store, [self.a, self.b], "user")
            self.assertEqual(result["total"], 1)
            self.assertFalse(any(p["id"] == first_pipeline for p in result["pipelines"]))

    def test_display_cap_preserves_full_counts_and_global_order(self):
        with self.store.db() as c:
            for i in range(205):
                contact = f"synthetic-contact-{i}"
                c.execute("INSERT INTO contacts(id,name,phone) VALUES(?,?,?)", (contact, contact, f"55000{i:08d}"))
                c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at) VALUES(?,?,?,'Mensagem','in','received',?)",
                          (f"synthetic-message-{i}", self.channel if i % 2 else self.second, contact, i + 1))
        result = combined(self.store, [self.a, self.b], "user")
        self.assertEqual(len(result["conversations"]), 200)
        self.assertEqual(result["total"], 205)
        self.assertEqual(result["counts"]["waiting"], 205)
        self.assertEqual(result["conversations"][0]["created_at"], 205)
        self.assertEqual(result["conversations"][-1]["created_at"], 6)

    def test_invalid_selection_is_rejected(self):
        for doctors in (None, "all", [], [True], [""], ["x" * 65], [self.a] * 101):
            with self.subTest(doctors=doctors), self.assertRaises(ValueError):
                combined(self.store, doctors, "user")
        with self.assertRaises(LookupError):
            combined(self.store, ["nonexistent"], "user")

    def test_search_finds_existing_funnel_contacts_without_starting_or_duplicating_chat(self):
        lead_id = fixture.CrmTests.lead(self)
        with self.store.db() as c:
            contact = c.execute("SELECT contact_id FROM leads WHERE id=?", (lead_id,)).fetchone()[0]
            c.execute("UPDATE contacts SET name='Debora Exemplo' WHERE id=?", (contact,))
            before = {table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                      for table in ("leads", "contacts", "messages", "conversation_resolutions")}
        for search in ("débora", "+55 (00) 00000-0002"):
            result = combined(self.store, [self.a], "user", search=search)
            self.assertEqual(result["conversations"], [])
            self.assertEqual(result["counts"]["all"], 0)
            self.assertEqual(result["lead_results_total"], 1)
            item = result["lead_results"][0]
            self.assertEqual((item["id"], item["contact_id"], item["doctor_id"]), (lead_id, contact, self.a))
            self.assertEqual(item["pipeline_name"], self.p["name"])
            self.assertEqual(item["stage_name"], self.p["stages"][0]["name"])
            self.assertFalse(item["has_conversation"])
            thread = self.store.conversation(self.a, self.channel, contact)
            self.assertEqual(thread["messages"], [])
            self.assertTrue(thread["reply_allowed"])
        self.assertEqual(combined(self.store, [self.a], "user")["lead_results"], [])
        with self.store.db() as c:
            self.assertEqual({table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in before}, before)

    def test_search_deduplicates_contacts_and_does_not_repeat_existing_conversations(self):
        fixture.CrmTests.lead(self)
        other = self.store.listing(self.a)["pipelines"][1]
        fixture.CrmTests.lead(self, pipeline=other)
        result = combined(self.store, [self.a], "user", search="Cliente Exemplo")
        self.assertEqual(result["lead_results_total"], 1)
        thread = self.incoming(self.channel, "5500000000002", "existing")
        result = combined(self.store, [self.a], "user", search="Cliente Exemplo")
        self.assertEqual(len(result["conversations"]), 1)
        self.assertEqual(result["lead_results"], [])
        self.store.close_conversation({"doctor_id": self.a, "channel_id": self.channel,
            "contact_id": thread["contact_id"], "cursor": thread["seq"]}, "user")
        result = combined(self.store, [self.a], "user", search="Cliente Exemplo", kind="unread")
        self.assertEqual(result["conversations"], [])
        self.assertTrue(result["lead_results"][0]["has_conversation"])

    def test_search_respects_funnel_professional_institute_and_business_access(self):
        allowed = fixture.CrmTests.lead(self)
        hidden_pipeline = self.store.listing(self.a)["pipelines"][1]
        fixture.CrmTests.lead(self, number="5500000000005", pipeline=hidden_pipeline)
        b_pipeline = self.store.listing(self.b)["pipelines"][0]
        fixture.CrmTests.lead(self, number="5500000000006", doctor=self.b, pipeline=b_pipeline)
        with crm_access({self.a: [self.p["id"]], self.b: None}, self.a):
            result = combined(self.store, [self.a, self.b], "user", search="Cliente")
            self.assertEqual(result["lead_results_total"], 2)
            self.assertEqual({r["pipeline_id"] for r in result["lead_results"]}, {self.p["id"], b_pipeline["id"]})
            result = combined(self.store, [self.a, self.b], "user", channel=self.second, search="Cliente")
            self.assertEqual({r["doctor_id"] for r in result["lead_results"]}, {self.b})
        with self.store.db() as c:
            c.execute("UPDATE pipelines SET institute_key='private',course_key='online' WHERE id=?", (hidden_pipeline["id"],))
        with institute_access([]):
            result = combined(self.store, [self.a], "user", search="Cliente")
            self.assertEqual([r["id"] for r in result["lead_results"]], [allowed])
        self.assertEqual(combined(self.store, [self.a], "user", search="Cliente", business="course")["lead_results_total"], 1)
        self.assertEqual(combined(self.store, [self.a], "user", search="Cliente", business="clinic")["lead_results_total"], 1)

    def test_search_respects_filters_active_phone_and_deleted_leads(self):
        first = fixture.CrmTests.lead(self)
        removed = fixture.CrmTests.lead(self, number="5500000000005")
        with self.store.db() as c:
            c.execute("UPDATE leads SET deleted_at=1 WHERE id=?", (removed,))
            c.execute("UPDATE contacts SET service_location='sao-paulo' WHERE id=(SELECT contact_id FROM leads WHERE id=?)", (first,))
            contact = c.execute("SELECT contact_id FROM leads WHERE id=?", (first,)).fetchone()[0]
            c.execute("INSERT INTO contact_phone_settings VALUES(?,?,1,1,'user')", (contact, "5511987654321"))
        legacy = combined(self.store, [self.a], "user", search="5500000000002")
        self.assertEqual(legacy["lead_results"][0]["phone"], "5511987654321")
        result = combined(self.store, [self.a], "user", search="(11) 98765-4321", filters={"service_location": "sao-paulo"})
        self.assertEqual([r["id"] for r in result["lead_results"]], [first])
        self.assertEqual(result["lead_results"][0]["phone"], "5511987654321")
        self.assertEqual(combined(self.store, [self.a], "user", search="Cliente", filters={"service_location": "capelinha"})["lead_results_total"], 0)

    def test_search_beyond_loaded_leads_is_bounded_without_losing_total(self):
        with self.store.db() as c:
            for i in range(1050):
                c.execute("INSERT INTO contacts(id,name,phone) VALUES(?,?,?)", (f"contact-{i}", f"Busca {i}", f"55001{i:08d}"))
                c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,updated_at,created_at) VALUES(?,?,?,?,?,'Contato',?,?)",
                          (f"lead-{i}", self.a, self.p["id"], self.p["stages"][0]["id"], f"contact-{i}", i, i))
        result = combined(self.store, [self.a], "user", search="Busca")
        self.assertEqual(result["lead_results_total"], 1050)
        self.assertEqual(len(result["lead_results"]), 50)
        self.assertEqual(result["lead_results"][0]["name"], "Busca 1049")
        self.assertEqual(combined(self.store, [self.a], "user", search="Busca 0")["lead_results"][0]["id"], "lead-0")


if __name__ == "__main__":
    unittest.main()
