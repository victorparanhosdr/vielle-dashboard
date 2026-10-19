import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlparse

from crm_api import handle_request
from tests import test_crm as base


class ConversationSearchTests(unittest.TestCase):
    lead = base.CrmTests.lead

    def setUp(self):
        base.CrmTests.setUp(self)
        key = base.CrmTests.lead(self)
        self.contact = self.store.detail(key, self.a)["lead"]["contact_id"]

    def message(self, number, body="Quero avaliação", direction="in", **changes):
        values = {"channel": self.channel, "contact": self.contact, "status": "received" if direction == "in" else "sent",
                  "created": time.time() - 1000 + number, "campaign": None, **changes}
        with self.store.db() as c:
            return c.execute("""INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at,campaign_id)
                VALUES(?,?,?,?,?,?,?,?)""", (f"message-{number}", values["channel"], values["contact"], body, direction,
                values["status"], values["created"], values["campaign"])).lastrowid

    def search(self, term="avaliacao", **changes):
        return self.store.conversation_search(self.a, self.channel, self.contact, term, **changes)

    def test_search_entire_saved_history_literal_unicode_and_direction(self):
        oldest = self.message(0, "Minha AVALIAÇÃO foi ótima. Usei 50%_ do produto.")
        for i in range(1, 110):
            self.message(i, "Outra mensagem")
        outgoing = self.message(111, "Vamos marcar a avaliação?", "out")
        latest = self.store.conversation(self.a, self.channel, self.contact)
        self.assertNotIn(oldest, [m["seq"] for m in latest["messages"]])
        self.assertEqual([m["seq"] for m in self.search()["results"]], [outgoing, oldest])
        self.assertEqual(self.search(direction="in")["results"], [{"seq": oldest}])
        self.assertEqual(self.search(direction="out")["results"], [{"seq": outgoing}])
        self.assertEqual(self.search("50%_")["total"], 1)
        self.assertEqual(self.search("%' OR 1=1 --")["total"], 0)
        self.assertEqual(self.search("ausente")["total"], 0)

    def test_pagination_same_timestamp_has_no_duplicates_or_1000_limit(self):
        stamp = time.time()
        expected = [self.message(i, created=stamp) for i in range(1021)]
        rows, before = [], 0
        while True:
            page = self.search(before=before)
            self.assertEqual(page["total"], 1021)
            self.assertLessEqual(len(page["results"]), 50)
            rows.extend(r["seq"] for r in page["results"])
            if not page["has_more"]:
                break
            before = page["next_before"]
        self.assertEqual(rows, list(reversed(expected)))

    def test_search_excludes_other_threads_hidden_drafts_and_rejects_foreign_markers(self):
        own = self.message(0)
        other = self.store.channel({"doctor_id": self.a, "name": "Segundo", "phone": "5500000000003"})
        foreign = self.message(1, channel=other)
        for i, status in enumerate(("cancelled", "skipped"), 2):
            self.message(i, direction="out", status=status)
        campaign = base.CrmTests.campaign(self)
        with self.store.db() as c:
            c.execute("UPDATE messages SET body='Quero avaliação' WHERE campaign_id=?", (campaign,))
        self.assertEqual(self.search()["results"], [{"seq": own}])
        for method in (lambda: self.search(before=foreign),
                       lambda: self.store.conversation(self.a, self.channel, self.contact, around=foreign),
                       lambda: self.store.conversation_search(self.b, self.channel, self.contact, "avaliacao")):
            with self.assertRaises(ValueError):
                method()
        for changes in ({"direction": "invalid"}, {"before": -1}, {"before": True}):
            with self.assertRaises(ValueError):
                self.search(**changes)
        for term in ("", "x" * 161):
            with self.assertRaises(ValueError):
                self.search(term)

    def test_context_keeps_chronological_neighbors_and_never_resolves_conversation(self):
        stamp = time.time()
        ids = [self.message(i, created=stamp) for i in range(100)]
        status = self.store.conversation(self.a, self.channel, self.contact)
        self.search()
        context = self.store.conversation(self.a, self.channel, self.contact, around=ids[40])
        self.assertEqual([m["seq"] for m in context["messages"]], ids[20:61])
        self.assertFalse(context["has_older"])
        self.assertEqual(context["unread"], status["unread"])
        self.assertEqual(context["resolution"], "open")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM conversation_resolutions").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.store.conversation(self.a, self.channel, self.contact, ids[60], around=ids[40])

    def test_api_keeps_crm_permission_checks_for_search_and_context(self):
        marker = self.message(0)
        for action, extra in (("conversation-search", {"search": "avaliacao"}), ("conversation", {"around": marker})):
            h = Mock()
            h.command = "GET"
            h.current_user = {"id": "operator", "is_master": False}
            h.server.auth_store.has_permission.return_value = True
            parsed = urlparse("/api/crm/" + action + "?" + urlencode({"clinic": "vielle", "doctor": self.a,
                "channel": self.channel, "contact": self.contact, **extra}))
            with patch("crm_api.store_for", return_value=self.store):
                handle_request(h, parsed)
            self.assertTrue(h.auth_json.call_args.args[0]["ok"])
            h.server.auth_store.has_permission.return_value = False
            handle_request(h, parsed)
            self.assertEqual(h.auth_json.call_args.args[1], 403)
