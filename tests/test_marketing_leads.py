from pathlib import Path
import secrets
import tempfile
import unittest

from auth_store import AuthStore
from marketing_leads import MarketingLeadStore, RequestLimiter, validate_request


class MarketingLeadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = AuthStore(Path(self.temp.name) / "auth.sqlite3")
        self.auth.initialize()
        self.actor = self.auth.create_user("Master Test", "master", "Long-enough-password!", is_master=True)
        self.store = MarketingLeadStore(self.auth)
        self.store.initialize()
        self.payload = {"request_id": secrets.token_hex(16), "name": "Ana Exemplo", "organization": "Clínica Exemplo",
                        "email": "ANA@example.test", "phone": "(11) 99999-0000", "plan": "Demonstração", "privacy": True,
                        "message": "Gostaria de conhecer.", "attribution": {"utm_source": "campanha"}, "source_path": "/inicio"}

    def test_normalization_persistence_and_private_identity(self):
        key = self.store.submit(self.payload)
        row = self.store.listing()["leads"][0]
        self.assertEqual(row["id"], key)
        self.assertEqual(row["phone"], "+5511999990000")
        self.assertEqual(row["email"], "ana@example.test")
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["privacy_version"], "2026-10-21")
        self.assertEqual(row["attribution"]["utm_source"], "campanha")
        self.assertNotIn("request_hash", row)
        self.assertNotIn("request_id", row)
        with self.auth.connection() as c:
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='contacts'").fetchone())

    def test_retries_are_idempotent_but_changed_requests_conflict(self):
        first = self.store.submit(self.payload)
        self.assertEqual(self.store.submit(self.payload), first)
        self.assertEqual(self.store.listing()["total"], 1)
        with self.assertRaises(ValueError):
            self.store.submit({**self.payload, "name": "Outra pessoa"})
        self.assertEqual(self.store.listing()["total"], 1)

    def test_status_notes_and_optimistic_revision_audited(self):
        key = self.store.submit(self.payload)
        self.store.update(key, {"status": "proposal", "notes": "Enviar proposta", "revision": 1}, self.actor)
        row = self.store.listing(status="proposal")["leads"][0]
        self.assertEqual((row["status"], row["notes"], row["revision"]), ("proposal", "Enviar proposta", 2))
        with self.assertRaises(ValueError):
            self.store.update(key, {"status": "won", "notes": "", "revision": 1}, self.actor)
        with self.assertRaises(LookupError):
            self.store.update("0" * 32, {"status": "new", "notes": "", "revision": 1}, self.actor)

    def test_filters_escape_sql_wildcards_and_paginate(self):
        for i in range(27):
            self.store.submit({**self.payload, "request_id": secrets.token_hex(16), "organization": f"Clínica {i}"})
        self.assertEqual(len(self.store.listing()["leads"]), 25)
        self.assertEqual(len(self.store.listing(page=2)["leads"]), 2)
        self.assertEqual(self.store.listing(search="% '_ OR 1=1")["total"], 0)
        self.assertEqual(self.store.listing(search="Clínica 26")["total"], 1)
        self.assertEqual(self.store.listing()["counts"]["new"], 27)

    def test_invalid_and_oversized_inputs(self):
        invalid = [{"privacy": False}, {"website": "filled"}, {"status": "won"}, {"name": ""}, {"organization": "x" * 141},
                   {"message": "x" * 1001}, {"plan": "fake"}, {"phone": "123"}, {"phone": "+5511999990000 ext 1"},
                   {"email": "x\r\nBcc:other@example.test"}, {"attribution": {"unexpected": "x"}},
                   {"attribution": {"utm_source": ["x"]}}, {"source_path": "/api/master/users"}, {"request_id": "bad"}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_request({**self.payload, **changes})
        for value in ([], None, "payload"):
            with self.assertRaises(ValueError):
                validate_request(value)

    def test_update_rejects_invalid_values(self):
        key = self.store.submit(self.payload)
        for changes in ({"status": "bad"}, {"status": []}, {"revision": True}, {"notes": "x" * 4001}, {"name": "changed"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.update(key, {"status": "new", "notes": "", "revision": 1, **changes}, self.actor)

    def test_rate_limit_does_not_store_raw_peer_and_is_per_peer(self):
        limiter = RequestLimiter()
        self.assertTrue(all(limiter.allow("peer-a") for _ in range(8)))
        self.assertFalse(limiter.allow("peer-a"))
        self.assertTrue(limiter.allow("peer-b"))
        self.assertNotIn("peer-a", limiter.requests)

    def test_proxy_peer_cap_allows_different_visitors(self):
        limiter = RequestLimiter(limit=120)
        self.assertTrue(all(limiter.allow("proxy") for _ in range(120)))
        self.assertFalse(limiter.allow("proxy"))
        contacts = RequestLimiter()
        self.assertTrue(all(contacts.allow("first@example.test") for _ in range(8)))
        self.assertFalse(contacts.allow("first@example.test"))
        self.assertTrue(contacts.allow("second@example.test"))


if __name__ == "__main__":
    unittest.main()
