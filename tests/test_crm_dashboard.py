import time
import unittest
from unittest.mock import Mock

from crm_dashboard import dashboard
from crm_worker import tick
from tests import test_crm_inbox as fixture


class DashboardTests(unittest.TestCase):
    setUp = fixture.InboxTests.setUp
    incoming = fixture.InboxTests.incoming
    payload = fixture.InboxTests.payload

    def test_wait_starts_at_first_unanswered_message_and_close_keeps_new_arrival(self):
        now = time.time()
        item = self.incoming(provider="first")
        first = self.payload(item)["cursor"]
        with self.store.db() as c:
            c.execute("UPDATE messages SET created_at=?,provider_timestamp=0 WHERE provider_id='first'", (now - 7200,))
        self.incoming(provider="second")
        with self.store.db() as c:
            c.execute("UPDATE messages SET created_at=?,provider_timestamp=0 WHERE provider_id='second'", (now - 120,))
        row = self.store.inbox(self.a, "user")["conversations"][0]
        self.assertAlmostEqual(row["waiting_since"], now - 7200)
        self.store.conversation(self.a, self.channel, item["contact_id"])
        self.assertEqual(dashboard(self.store, self.a, now=now)["longest_wait_seconds"], 7200)
        self.store.close_conversation(self.payload(item, cursor=first), "user")
        report = dashboard(self.store, self.a, now=now)
        self.assertEqual(report["pending_clients"], 1)
        self.assertEqual(report["longest_wait_seconds"], 120)
        self.store.close_conversation(self.payload(item), "user")
        report = dashboard(self.store, self.a, now=now)
        self.assertEqual(report["pending_clients"], 0)
        self.assertEqual(report["waiting_buckets"], [0, 0, 0, 0])

    def test_two_numbers_one_client_and_doctor_isolation(self):
        self.incoming()
        second = self.store.channel({"doctor_id": self.a, "name": "Outro", "phone": "5500000000009"})
        self.incoming(second, provider="other-number")
        report = dashboard(self.store, self.a)
        self.assertEqual(report["pending_clients"], 1)
        self.assertEqual(report["pending_conversations"], 2)
        self.assertEqual(dashboard(self.store, self.b)["pending_clients"], 0)
        with self.assertRaises(LookupError):
            dashboard(self.store, "nonexistent-doctor")

    def test_only_accepted_manual_replies_count_for_operators(self):
        item = self.incoming()
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open' WHERE id=?", (self.channel,))
        self.store.reply(self.payload(item, body="Olá", request_id="metrics-reply"), "operator-1", True)
        self.assertEqual(dashboard(self.store, self.a, include_operators=True)["manual_replies"], 0)
        gateway = Mock()
        gateway.send.return_value = "metrics-out"
        self.assertTrue(tick(self.store, gateway))
        report = dashboard(self.store, self.a, include_operators=True)
        self.assertEqual(report["manual_replies"], 1)
        self.assertEqual(report["pending_clients"], 0)
        self.assertEqual(report["replied_clients"], 1)
        self.assertEqual(report["operators"][0]["id"], "operator-1")
        self.assertEqual(report["operators"][0]["contacts"], 1)
        self.assertIsNone(dashboard(self.store, self.a)["operators"])

    def test_dashboard_counts_more_than_inbox_display_cap(self):
        for index in range(205):
            self.incoming(number=f"5500000{index:06d}", provider=f"metrics-{index}")
        inbox = self.store.inbox(self.a, "user")
        self.assertEqual(len(inbox["conversations"]), 200)
        self.assertEqual(inbox["counts"]["waiting"], 205)
        self.assertEqual(dashboard(self.store, self.a)["pending_clients"], 205)

    def test_report_period_and_buckets(self):
        now = time.time()
        for i, seconds in enumerate((30, 1000, 5000, 90000)):
            self.incoming(number=f"55000000100{i}", provider=f"age-{i}")
            with self.store.db() as c:
                c.execute("UPDATE messages SET created_at=?,provider_timestamp=0 WHERE provider_id=?", (now - seconds, f"age-{i}"))
        report = dashboard(self.store, self.a, 30, now=now)
        self.assertEqual(len(report["daily"]), 30)
        self.assertEqual(report["waiting_buckets"], [1, 1, 1, 1])
        for invalid in (0, -1, 365, True, "bad"):
            with self.assertRaises(ValueError):
                dashboard(self.store, self.a, invalid)


if __name__ == "__main__":
    unittest.main()
