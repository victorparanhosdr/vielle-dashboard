import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class QuoteFollowupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(app, "clinic_db_path", return_value=Path(temp.name) / "clinic.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        app.init_db()
        self.conn = app.db()
        self.addCleanup(self.conn.close)

    def add(self, uuid, kind="sale_quote", status="inactive", **fields):
        raw = {"uuid": uuid, "type": kind, "status": status, "quote_date": "2026-06-19",
               "buyer": {"uuid": "p1", "name": "Paciente Teste"}, "nominal_amount": 1330000, **fields}
        self.conn.execute("insert into clinica_sales(uuid,patient_uuid,type,sale_date,total,raw_json,synced_at) values(?,?,?,'2026-06-19',10400,?,0)",
                          (uuid, "p1", kind, json.dumps(raw)))
        self.conn.commit()

    def report(self):
        return app.build_quote_followup(self.conn, "2026-01-01", "2026-12-31", [])

    def test_inactive_sale_is_not_pending_quote(self):
        self.add("converted-sale", kind="sale")
        self.add("actual-open-quote")
        result = self.report()
        self.assertEqual([r["quote_key"] for r in result["items"]], ["actual-open-quote"])
        self.assertEqual(result["totals"]["total"], 1)

    def test_won_source_keeps_contacts_and_manual_history(self):
        self.add("won-quote", status="won")
        self.conn.execute("insert into quote_followup_contacts(quote_key,contact_date,created_at) values('won-quote','2026-06-20',1)")
        self.conn.execute("insert into quote_followup_status(quote_key,status,status_date,created_at) values('won-quote','active','2026-06-20',1)")
        self.conn.commit()
        result = self.report()
        self.assertEqual(result["totals"]["total"], 0)
        self.assertEqual(result["totals"]["won"], 1)
        self.assertEqual(result["items"][0]["contact_count"], 1)
        self.assertEqual(self.conn.execute("select status from quote_followup_status").fetchone()[0], "active")
        self.assertEqual(self.conn.execute("select count(*) from quote_followup_contacts").fetchone()[0], 1)

    def test_later_purchase_does_not_close_unrelated_quote(self):
        self.add("open-quote")
        self.add("unrelated-sale", kind="sale", status="active")
        self.assertEqual(self.report()["totals"]["total"], 1)

    def test_explicit_conversion_and_cancelled_sale(self):
        self.add("converted-quote")
        self.add("other-quote")
        self.add("linked-sale", kind="sale", sale_quote_uuid="converted-quote")
        self.add("cancelled-sale", kind="sale", status="cancelled", quote_uuid="other-quote")
        result = self.report()
        self.assertEqual(result["totals"]["won"], 1)
        self.assertEqual(result["totals"]["total"], 1)

    def test_source_lost_and_manual_won(self):
        self.add("lost-quote", status="lost")
        self.add("manual-quote")
        self.conn.execute("insert into quote_followup_status(quote_key,status,status_date,created_at) values('manual-quote','won','2026-06-20',1)")
        self.conn.commit()
        result = self.report()
        self.assertEqual(result["totals"]["won"], 1)
        self.assertEqual(result["totals"]["lost"], 1)
        self.assertEqual(result["totals"]["total"], 0)

    def test_quote_identifier_supported_on_sync(self):
        self.assertTrue(app.save_clinica_sale(self.conn, {"sale_quote_uuid": "q", "type": "sale_quote", "status": "won", "quote_date": "2026-06-19"}, 1))
        self.assertEqual(self.report()["totals"]["won"], 1)


if __name__ == "__main__":
    unittest.main()
