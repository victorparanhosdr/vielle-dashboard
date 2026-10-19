import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlparse

from crm_api import handle_request
from crm_filters import parse_filters
from crm_kanban import overview, page, quotes
from crm_store import Conflict
from tests import test_crm as base


class KanbanTests(unittest.TestCase):
    setUp = base.CrmTests.setUp
    lead = base.CrmTests.lead

    def seed(self, count, stage=None, pipeline=None, doctor=None, prefix="lead"):
        pipeline = pipeline or self.p
        doctor = doctor or self.a
        stage = stage or pipeline["stages"][0]["id"]
        with self.store.db() as c:
            for i in range(count):
                key = f"{prefix}-{i:05}"
                number = f"550000{sum(map(ord, prefix)):04}{i:05}"
                c.execute("INSERT INTO contacts(id,name,phone) VALUES(?,?,?)", (key, f"Cliente {prefix} {i:05}", number))
                c.execute("INSERT INTO contact_phones VALUES(?,?)", (number, key))
                c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (key, doctor, pipeline["id"], stage, key, "Retorno", self.now, self.now + i))

    def board(self, filters=None, pipeline=None):
        return self.store.listing(self.a, filters, kanban_pipeline=(pipeline or self.p)["id"])

    def test_initial_load_is_20_per_stage_with_complete_totals(self):
        self.seed(45)
        self.seed(26, stage=self.p["stages"][1]["id"], prefix="second")
        data = self.board()
        self.assertEqual(len(data["leads"]), 40)
        self.assertEqual(data["kanban"]["summary"]["total"], 71)
        self.assertEqual([c["total"] for c in data["kanban"]["columns"][:2]], [45, 26])
        self.assertEqual([c["loaded"] for c in data["kanban"]["columns"][:2]], [20, 20])
        self.assertTrue(all(c["next_cursor"] for c in data["kanban"]["columns"][:2]))
        self.assertEqual(data["kanban"]["columns"][-1]["loaded"], 0)
        self.assertEqual(len(self.store.listing(self.a)["leads"]), 71)

    def test_all_1105_leads_are_reachable_without_repeats_or_truncation(self):
        # Pure paging exercises all pages without 56 repeated patient synchronizations.
        items = [{"id": f"lead-{i}", "revision": 1} for i in range(1105)]
        scope = [self.a, self.p["id"], "stage", parse_filters()]
        result = page(items, scope)
        found = [l["id"] for l in result["leads"]]
        while result["next_cursor"]:
            result = page(items, scope, result["next_cursor"])
            self.assertLessEqual(len(result["leads"]), 20)
            found.extend(l["id"] for l in result["leads"])
        self.assertEqual(found, [l["id"] for l in items])
        self.assertEqual(result["loaded"], 1105)
        self.seed(1105)
        data = self.board()
        self.assertEqual(data["kanban"]["summary"]["total"], 1105)
        self.assertEqual(len(data["leads"]), 20)
        self.assertEqual(len(self.store.listing(self.a)["leads"]), 1000)

    def test_next_page_respects_order_filters_pipeline_and_doctor(self):
        self.seed(45)
        self.seed(25, pipeline=self.store.listing(self.a)["pipelines"][1], prefix="other")
        foreign = self.store.listing(self.b)["pipelines"][0]
        self.seed(25, doctor=self.b, pipeline=foreign, prefix="foreign")
        filters = {"search": "Cliente lead"}
        initial = self.board(filters)
        column = initial["kanban"]["columns"][0]
        next_page = self.store.kanban_page(self.a, self.p["id"], column["stage_id"], filters, column["next_cursor"])
        self.assertEqual([l["id"] for l in initial["leads"]], [f"lead-{i:05}" for i in range(44, 24, -1)])
        self.assertEqual([l["id"] for l in next_page["leads"]], [f"lead-{i:05}" for i in range(24, 4, -1)])
        last = self.store.kanban_page(self.a, self.p["id"], column["stage_id"], filters, next_page["next_cursor"])
        self.assertEqual(len(last["leads"]), 5)
        self.assertFalse(last["next_cursor"])
        with self.assertRaises(ValueError):
            self.store.kanban_page(self.a, foreign["id"], foreign["stages"][0]["id"])
        with self.assertRaises(ValueError):
            self.store.kanban_page(self.a, self.p["id"], foreign["stages"][0]["id"])

    def test_changed_or_cross_scope_cursor_requires_refresh(self):
        self.seed(25)
        initial = self.board()
        column = initial["kanban"]["columns"][0]
        cursor = column["next_cursor"]
        for filters in ({"search": "Cliente"}, {"sort": "spent"}):
            with self.subTest(filters=filters), self.assertRaises(Conflict):
                self.store.kanban_page(self.a, self.p["id"], column["stage_id"], filters, cursor)
        with self.assertRaises(Conflict):
            self.store.kanban_page(self.a, self.p["id"], self.p["stages"][1]["id"], {}, cursor)
        with self.store.db() as c:
            c.execute("UPDATE leads SET revision=revision+1 WHERE id='lead-00000'")
        with self.assertRaises(Conflict):
            self.store.kanban_page(self.a, self.p["id"], column["stage_id"], {}, cursor)
        for invalid in ("bad", "{}", "[]", '[1,2]', "x" * 513):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.kanban_page(self.a, self.p["id"], column["stage_id"], {}, invalid)

    def test_financial_filters_and_sorting_apply_before_paging(self):
        self.seed(45)
        def attach(c, leads, name, include_search=False):
            for lead in leads:
                amount = (44 - int(lead["id"].split("-")[-1])) * 100
                lead["customer_history"] = {"sales_state": "ready", "sales_cents": amount, "sales_count": 1,
                                            "quotes_state": "ready", "pending_quotes_cents": 0}
                lead["customer_value"] = {"tier": "bronze"}
        filters = {"sort": "spent", "min_spent_cents": 500}
        with patch.object(self.store, "attach_customer_values", side_effect=attach):
            data = self.board(filters)
            self.assertEqual(data["kanban"]["summary"]["total"], 40)
            self.assertEqual([l["id"] for l in data["leads"]], [f"lead-{i:05}" for i in range(20)])
            column = data["kanban"]["columns"][0]
            more = self.store.kanban_page(self.a, self.p["id"], column["stage_id"], filters, column["next_cursor"])
            self.assertEqual([l["id"] for l in more["leads"]], [f"lead-{i:05}" for i in range(20, 40)])
            self.assertFalse(more["next_cursor"])

    def test_full_financial_totals_are_deduplicated_not_just_visible_cards(self):
        stages = self.p["stages"]
        items = [{"id": str(i), "revision": 1, "pipeline_id": self.p["id"], "stage_id": stages[0]["id"],
                  "contact_id": str(i), "kind": "open", "next_action": "2026-01-01", "updated_at": 1,
                  "customer_value": {"tier": "gold"}, "customer_history": {"quotes_state": "ready", "pending_quotes_cents": 101}}
                 for i in range(25)]
        items.append({**items[0], "id": "duplicate", "stage_id": stages[1]["id"]})
        visible, result = overview(items, self.p, self.a, parse_filters(), "2026-10-19")
        self.assertEqual(len(visible), 21)
        self.assertEqual(result["summary"]["total"], 26)
        self.assertEqual(result["summary"]["quotes"], {"cents": 2525, "pending": 0})
        self.assertEqual(result["summary"]["tiers"]["gold"], 25)
        self.assertEqual(result["summary"]["overdue"], 26)
        self.assertEqual(result["summary"]["stale"], 26)
        self.assertEqual(quotes([{"contact_id": "bad", "customer_history": {"quotes_state": "partial"}},
                                 {"contact_id": "new", "customer_history": {"quotes_state": "unlinked"}}]), {"cents": 0, "pending": 1})

    def test_api_requires_master_and_exposes_bounded_kanban_only_on_request(self):
        self.seed(25)
        def handler(master=True):
            h = Mock()
            h.current_user = {"id": "operator", "nome": "Equipe", "is_master": master}
            h.command = "GET"
            return h
        query = {"clinic": "vielle", "doctor": self.a, "pipeline": self.p["id"], "stage": self.p["stages"][0]["id"]}
        with patch("crm_api.store_for", return_value=self.store):
            h = handler(False)
            handle_request(h, urlparse("/api/crm/kanban-page?" + urlencode(query)))
            self.assertEqual(h.auth_json.call_args.args[1], 403)
            h = handler()
            handle_request(h, urlparse("/api/crm/kanban-page?" + urlencode(query)))
            self.assertEqual(len(h.auth_json.call_args.args[0]["leads"]), 20)
            with patch("crm_api.EvolutionGateway"), patch("crm_api.sends_enabled", return_value=False):
                h = handler()
                handle_request(h, urlparse("/api/crm?" + urlencode({**query, "view": "kanban"})))
                self.assertEqual(len(h.auth_json.call_args.args[0]["leads"]), 20)
                self.assertIn("kanban", h.auth_json.call_args.args[0])
                h = handler()
                handle_request(h, urlparse("/api/crm?" + urlencode(query)))
                self.assertEqual(len(h.auth_json.call_args.args[0]["leads"]), 25)
                self.assertNotIn("kanban", h.auth_json.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
