import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

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
        self.assertEqual(result["totals"]["total"], 1)
        self.assertTrue(next(r for r in result["items"] if r["quote_key"] == "lost-quote")["pending"])

    def test_open_lost_expired_are_pending_but_won_is_not(self):
        for key, status in (("open", "open"), ("lost", "lost"), ("expired", "due"), ("won", "won")):
            self.add(key, status=status)
        result = self.report()
        self.assertEqual(result["totals"]["total"], 3)
        self.assertEqual(result["totals"]["amount"], 39900)
        self.assertEqual(result["totals"]["expired"], 1)
        self.assertEqual({r["quote_key"] for r in result["items"] if r["pending"]}, {"open", "lost", "expired"})

    def test_status_aliases_and_expiration(self):
        for status in ("due", "expired", "overdue", "vencido"):
            with self.subTest(status=status):
                self.assertEqual(app.quote_source_status({"type": "sale_quote", "status": status}), "expired")
        self.assertEqual(app.quote_source_status({"type": "sale_quote", "status": "open", "due_date": "2000-01-01"}), "expired")
        self.assertEqual(app.quote_source_status({"type": "sale_quote", "status": "won", "due_date": "2000-01-01"}), "won")
        self.assertEqual(app.quote_source_status({"type": "sale_quote", "status": "lost", "due_date": "2000-01-01"}), "lost")
        self.assertIsNone(app.quote_source_status({"type": "sale", "status": "due"}))

    def test_final_amount_matches_experts_instead_of_amount_before_discount(self):
        self.add("discounted", final_amount=1040000)
        self.assertEqual(self.report()["totals"]["amount"], 10400)
        self.add("free", final_amount=0)
        self.assertEqual(self.report()["totals"]["amount"], 10400)

    def test_manual_lost_keeps_contacts_and_is_pending(self):
        self.add("q")
        self.conn.execute("insert into quote_followup_status(quote_key,status,status_date,created_at) values('q','lost','2026-06-20',1)")
        self.conn.execute("insert into quote_followup_contacts(quote_key,contact_date,created_at) values('q','2026-06-20',1)")
        self.conn.commit()
        result = self.report()
        self.assertTrue(result["items"][0]["pending"])
        self.assertEqual(result["totals"]["contacted"], 1)
        self.assertEqual(result["items"][0]["contacts"][0]["contact_date"], "2026-06-20")
        self.assertEqual(self.conn.execute("select status from quote_followup_status").fetchone()[0], "lost")

    def test_manual_won_wins_over_lost_or_expired_source(self):
        for status in ("lost", "due"):
            self.add(status, status=status)
            self.conn.execute("insert into quote_followup_status(quote_key,status,status_date,created_at) values(?,'won','2026-06-20',1)", (status,))
        self.conn.commit()
        self.assertEqual(self.report()["totals"]["total"], 0)

    def test_linked_sale_closes_expired_quote(self):
        self.add("expired", status="due")
        self.add("sale", kind="sale", sale_quote_uuid="expired")
        self.assertEqual(self.report()["totals"]["total"], 0)

    def test_quote_sync_uses_interval_and_pagination_metadata(self):
        payloads = [
            {"data": [{"id": 1, "quote_date": "2026-01-02", "status": "due", "final_amount": 720000}], "meta": {"last_page": 2}},
            {"data": [{"id": 2, "quote_date": "2026-09-10", "status": "won", "final_amount": 360000}], "meta": {"last_page": 2}},
        ]
        with patch.object(app, "clinica_request", side_effect=payloads) as request:
            self.assertEqual(app.sync_clinica_sale_quotes_period("2026-01-01", "2026-09-10"), 2)
        for page, call in enumerate(request.call_args_list, 1):
            query = parse_qs(urlsplit(call.args[0]).query)
            self.assertEqual(query["search[interval][]"], ["2026-01-01", "2026-09-10"])
            self.assertNotIn("starts_at", query)
            self.assertNotIn("status", query)
            self.assertEqual(query["page"], [str(page)])
            self.assertEqual(call.kwargs["api_prefix"], "/api")
        self.assertEqual(self.report()["totals"]["total"], 1)
        self.assertEqual(self.report()["totals"]["amount"], 7200)

    def test_failed_quote_sync_does_not_delete_existing_records(self):
        self.add("existing", status="due")
        with patch.object(app, "clinica_request", side_effect=RuntimeError("sales-quotes: acesso negado")):
            with self.assertRaises(RuntimeError):
                app.sync_clinica_sale_quotes_period("2026-01-01", "2026-09-10")
        self.assertEqual(self.report()["totals"]["total"], 1)

    def test_regular_sync_requests_quotes_from_year_start(self):
        with patch.object(app, "sync_clinica_list", return_value=0), patch.object(app, "sync_clinica_period", return_value=(0, 0, 0, 0, 0, [])) as sync:
            app.sync_clinica_experts("2026-09-01", "2026-09-10")
        sync.assert_called_once_with("2026-09-01", "2026-09-10", quote_date_from="2026-01-01")

    def test_failed_sync_is_visible_without_exposing_error_details(self):
        self.conn.execute("insert into clinica_sync_log(started_at,ok,message) values(1,1,'Aviso: sales-quotes acesso negado. Detalhe privado')")
        self.conn.commit()
        self.assertIn("incompleta", self.report()["sync_warning"])
        self.assertNotIn("privado", self.report()["sync_warning"])
        self.conn.execute("insert into clinica_sync_log(started_at,ok,message) values(2,1,'753 orçamentos sincronizados')")
        self.conn.commit()
        self.assertEqual(self.report()["sync_warning"], "")

    def test_quote_identifier_supported_on_sync(self):
        self.assertTrue(app.save_clinica_sale(self.conn, {"sale_quote_uuid": "q", "type": "sale_quote", "status": "won", "quote_date": "2026-06-19"}, 1))
        self.assertEqual(self.report()["totals"]["won"], 1)


if __name__ == "__main__":
    unittest.main()
