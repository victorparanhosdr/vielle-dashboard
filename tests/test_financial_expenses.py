import json
import sqlite3
import unittest

from financial_expenses import build_expenses


class ExpenseTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            create table clinica_sales(patient_uuid text, sale_date text, type text, raw_json text);
            create table clinica_bills(uuid text, type text, emission_date text, raw_json text, synced_at int);
            create table clinica_parcels(uuid text, bill_uuid text, type text, status text,
                paid_at text, due_date text, raw_json text, synced_at int);
        """)

    def add(self, key="a", emission="2026-09-10", due="2026-10-10", paid="2026-11-10", status="open", owner="a", **extra):
        parcel = {"uuid": "p-" + key, "status": status, "due_date": due, "compensation_date": paid,
                  "final_amount": 10000, "net_amount": 9700, **extra}
        raw = {"uuid": key, "type": "Conta", "seller": {"uuid": owner}, "emission_date": emission,
               "final_amount": 10000, "net_amount": 9700, "payment_methods": [{"parcels": [parcel]}]}
        self.conn.execute("insert into clinica_bills values(?,'Conta',?,?,10)", (key, emission, json.dumps(raw)))
        self.conn.execute("insert into clinica_parcels values(?,?,'Conta',?,?,?,?,11)",
                          (parcel["uuid"], key, status, paid, due, json.dumps(parcel)))

    def report(self, *owners):
        return build_expenses(self.conn, "2026-09-01", "2026-09-30", owners)

    def test_competence_paid_and_forecast_use_three_distinct_dates(self):
        self.add()
        self.add(key="old-paid", emission="2026-08-10", paid="2026-09-15", status="paid")
        self.add(key="old-open", emission="2026-08-10", due="2026-09-20", paid=None)
        r = self.report()
        self.assertEqual((r["competence_gross"], r["competence_net"], r["paid_net"], r["planned_net"]), (100, 97, 97, 97))
        self.assertEqual(r["daily"], [{"day": "2026-09-10", "total": 100}])

    def test_paid_status_without_actual_date_is_not_paid_by_due_date(self):
        self.add(due="2026-09-15", paid=None, status="paid", calc_compensation_date="2026-09-15")
        self.assertEqual(self.report()["paid_net"], 0)
        self.assertEqual(self.report()["planned_net"], 0)
        self.assertEqual(self.report()["pending"][0]["reason"], "date")

    def test_zero_balance_does_not_prove_payment(self):
        self.add(due="2026-09-15", paid=None, balance=0)
        self.assertEqual(self.report()["paid_net"], 0)
        self.assertEqual(self.report()["planned_net"], 0)

    def test_partial_forecast_uses_remaining_balance_not_full_title(self):
        self.add(due="2026-09-15", paid=None, status="partial", balance=4000)
        r = self.report()
        self.assertEqual(r["planned_net"], 40)
        self.assertEqual(r["competence_gross"], 100)

    def test_professional_scope_and_cancelled_parent_preserved(self):
        self.add(key="a", status="paid", paid="2026-09-15")
        self.add(key="b", status="paid", paid="2026-09-15", owner="b")
        self.assertEqual(self.report("a")["paid_net"], 97)
        self.assertEqual(self.report()["paid_net"], 194)
        self.conn.execute("update clinica_bills set raw_json=json_set(raw_json,'$.status','excluído') where uuid='a'")
        self.assertEqual(self.report()["paid_net"], 97)
        self.assertEqual(self.report()["competence_gross"], 100)

    def test_flat_and_embedded_parcels_not_duplicated(self):
        self.add(status="paid", paid="2026-09-15")
        self.assertEqual(self.report()["paid_net"], 97)

    def test_future_payment_and_received_manual_income_not_expenses(self):
        self.add(status="paid", paid="2099-09-15")
        self.add(key="income", status="received", paid="2026-09-15")
        self.assertEqual(self.report()["paid_net"], 0)
        self.assertEqual(self.report()["competence_gross"], 100)

    def test_missing_net_never_infers_zero_fees(self):
        self.add(status="paid", paid="2026-09-15", net_amount=None)
        self.assertEqual(self.report()["paid_net"], 0)
        self.assertEqual(self.report()["pending"][0]["reason"], "amount")

    def test_raw_title_data_only_in_chart_export(self):
        self.add()
        self.assertNotIn("raw_json", self.report()["details"][0])
        exported = build_expenses(self.conn, "2026-09-01", "2026-09-30", export=True)
        self.assertEqual(exported["details"][0]["raw_json"]["uuid"], "a")
