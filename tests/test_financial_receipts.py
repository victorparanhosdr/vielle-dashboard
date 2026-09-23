import json
import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

from financial_receipts import build_receipts, net_value, receipt_day


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            create table clinica_sales(patient_uuid text, sale_date text, total real, type text, raw_json text);
            create table clinica_bills(uuid text, type text, emission_date text, raw_json text, synced_at int);
            create table clinica_parcels(uuid text, bill_uuid text, type text, status text,
                paid_at text, raw_json text, synced_at int);
        """)

    def add(self, key="a", seller="doctor-a", buyer="patient", sale_day="2026-08-01",
            amount=10000, net=9700, paid="2026-09-15", status="received", **extra):
        sale = {"buyer": {"uuid": buyer}, "seller": {"uuid": seller}, "final_amount": amount}
        self.conn.execute("insert into clinica_sales values(?,?,?,'sale',?)",
                          (buyer, sale_day, amount / 100, json.dumps(sale)))
        parcel = {"uuid": key, "status": status, "final_amount": amount,
                  "net_amount": net, "fees_amount": amount - (net or 0), "due_date": "2026-09-01"}
        bill = {"person": {"uuid": buyer}, "final_amount": amount,
                "payment_methods": [{"parcels": [parcel]}]}
        self.conn.execute("insert into clinica_bills values(?,'Venda',?,?,10)",
                          ("bill-" + key, sale_day, json.dumps(bill)))
        dates = {"compensation_date": paid, **extra}
        self.conn.execute("insert into clinica_parcels values(?,?,'Venda',?,?,?,11)",
                          (key, "bill-" + key, status, paid, json.dumps(dates)))

    def report(self, *doctors):
        return build_receipts(self.conn, "2026-09-01", "2026-09-30", doctors)

    def test_net_fees_not_double_deducted_and_old_sale_received_this_month(self):
        self.add()
        r = self.report("doctor-a")
        self.assertEqual((r["net_total"], r["fees_total"], r["count"]), (97, 3, 1))
        self.assertEqual(r["status"], "complete")

    def test_professionals_do_not_mix_even_for_same_patient(self):
        self.add()
        self.add(key="b", seller="doctor-b", amount=20000, net=19000)
        self.assertEqual(self.report("doctor-a")["net_total"], 97)
        self.assertEqual(self.report("doctor-b")["net_total"], 190)
        self.assertEqual(self.report()["net_total"], 287)
        self.assertEqual(self.report("unknown")["net_total"], 0)

    def test_same_amount_different_sale_dates_do_not_cross_professionals(self):
        self.add()
        self.add(key="b", seller="doctor-b", sale_day="2026-08-02")
        self.assertEqual(self.report("doctor-a")["count"], 1)
        self.assertEqual(self.report("doctor-b")["count"], 1)

    def test_ambiguous_match_is_never_assigned_to_both_professionals(self):
        self.add()
        self.add(key="b", seller="doctor-b")
        for doctor in ("doctor-a", "doctor-b"):
            r = self.report(doctor)
            self.assertEqual(r["net_total"], 0)
            self.assertEqual(r["excluded"]["professional"], 2)
            self.assertEqual(r["status"], "partial")
        self.assertEqual(self.report()["net_total"], 194)

    def test_unlinked_bill_counted_only_for_all_professionals(self):
        self.add()
        self.conn.execute("delete from clinica_sales")
        self.assertEqual(self.report()["net_total"], 97)
        self.assertEqual(self.report("doctor-a")["net_total"], 0)
        self.assertEqual(self.report("doctor-a")["excluded"]["professional"], 1)

    def test_quotes_never_establish_professional_ownership(self):
        self.add()
        self.conn.execute("update clinica_sales set type='sale_quote'")
        self.assertEqual(self.report("doctor-a")["excluded"]["professional"], 1)

    def test_compensation_not_sale_execution_or_due_date(self):
        self.add(paid="2026-10-01", execution_date="2026-09-15")
        self.assertEqual(self.report()["count"], 0)
        with patch("financial_receipts.date", wraps=date) as clock:
            clock.today.return_value = date(2026, 10, 31)
            self.assertEqual(build_receipts(self.conn, "2026-10-01", "2026-10-31")["net_total"], 97)

    def test_future_compensation_is_not_money_already_received(self):
        self.add(paid="2026-09-30")
        with patch("financial_receipts.date", wraps=date) as clock:
            clock.today.return_value = date(2026, 9, 23)
            self.assertEqual(self.report()["net_total"], 0)

    def test_pending_late_cancelled_zero_balance_do_not_count(self):
        for status in ("open", "late", "cancelled", "refunded"):
            self.add(key=status, status=status, balance=0)
        self.assertEqual(self.report()["count"], 0)

    def test_latest_parcel_status_can_reopen_payment(self):
        self.add()
        self.conn.execute("update clinica_parcels set status='open'")
        self.assertEqual(self.report()["count"], 0)

    def test_newer_bill_status_wins_over_stale_parcel(self):
        self.add()
        self.conn.execute("update clinica_parcels set status='open',synced_at=9")
        self.assertEqual(self.report()["count"], 1)

    def test_standalone_and_embedded_parcel_counted_once(self):
        self.add()
        self.assertEqual(self.report()["count"], 1)

    def test_multiple_installments_only_count_received_in_period(self):
        self.add(key="paid", amount=5000, net=4800)
        self.add(key="open", amount=5000, net=4800, status="open")
        self.add(key="last-month", paid="2026-08-31")
        self.add(key="next-month", paid="2026-10-01")
        self.assertEqual(self.report()["net_total"], 48)

    def test_missing_date_does_not_fall_back_to_due_or_estimated_compensation(self):
        self.add(paid=None, calc_compensation_date="2026-09-15")
        self.conn.execute("update clinica_parcels set paid_at='2026-09-15'")
        r = self.report()
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["excluded"]["date"], 1)
        self.assertIsNone(receipt_day({"due_date": "2026-09-15"}))

    def test_net_amount_zero_is_not_replaced_with_gross(self):
        self.add(net=0)
        self.assertEqual(self.report()["net_total"], 0)
        self.assertEqual(self.report()["count"], 1)

    def test_net_fallback_only_with_explicit_fees(self):
        self.assertEqual(net_value({"final_amount": 10000, "fees_amount": 250}), 9750)
        self.assertEqual(net_value({"final_amount": 10000, "fees_amount": 0}), 10000)
        self.assertIsNone(net_value({"final_amount": 10000}))
        self.assertIsNone(net_value({"net_amount": "NaN"}))
        self.assertIsNone(net_value({"net_amount": -1}))

    def test_missing_money_is_flagged_instead_of_gross_fallback(self):
        self.add()
        self.conn.execute("update clinica_bills set raw_json=json_remove(raw_json,"
                          "'$.payment_methods[0].parcels[0].net_amount',"
                          "'$.payment_methods[0].parcels[0].fees_amount')")
        r = self.report()
        self.assertEqual(r["net_total"], 0)
        self.assertEqual(r["excluded"]["net_amount"], 1)

    def test_expenses_not_counted_as_receipts(self):
        self.add()
        self.conn.execute("update clinica_bills set type='Conta'")
        self.assertEqual(self.report()["count"], 0)


if __name__ == "__main__":
    unittest.main()
