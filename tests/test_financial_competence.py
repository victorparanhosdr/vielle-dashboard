import io
import json
import sqlite3
import unittest

from openpyxl import load_workbook
from financial_competence import build_report, query_options, export_workbook


class CompetenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            create table clinica_bills(uuid text, raw_json text, synced_at int);
            create table clinica_parcels(uuid text, bill_uuid text, status text, raw_json text, synced_at int);
            create table clinica_sales(patient_uuid text, sale_date text, type text, raw_json text);
        """)
        self.options = query_options({"date_from": ["2026-09-01"], "date_to": ["2026-09-30"]})

    def add(self, uuid="bill", kind="Venda", amount=10000, net=9700, day="2026-09-10", status="open", **extra):
        parcel = {"uuid": "parcel-" + uuid, "status": status, "due_date": "2026-10-10", "compensation_date": "2026-11-10"}
        raw = {"uuid": uuid, "type": kind, "final_amount": amount, "net_amount": net, "emission_date": day,
               "description": "Título fictício " + uuid, "category": {"name": "Serviços"},
               "person": {"uuid": "person-" + uuid, "name": "Contato fictício " + uuid},
               "payment_methods": [{"parcels": [parcel]}], **extra}
        self.conn.execute("insert into clinica_bills values(?,?,10)", (uuid, json.dumps(raw)))
        self.conn.execute("insert into clinica_parcels values(?,?,?,?,11)",
                          (parcel["uuid"], uuid, status, json.dumps(parcel)))
        return raw

    def report(self, **changes):
        return build_report(self.conn, {**self.options, **changes})

    def test_date_is_competence_not_payment_due_or_creation(self):
        self.add(created_at="2026-12-15")
        self.assertEqual(self.report(date_from="2026-09-10", date_to="2026-09-10")["totals"]["income"], 97)
        self.assertEqual(self.report(date_from="2026-10-01", date_to="2026-11-30")["count"], 0)
        self.add(uuid="explicit", competence_date="2026-09-11")
        self.assertEqual(self.report(date_from="2026-09-11", date_to="2026-09-11")["items"][0]["date_source"], "competence_date")

    def test_missing_emission_never_falls_back_to_due_or_created(self):
        self.add(emission_date=None, created_at="2026-09-10", due_date="2026-09-10")
        self.assertEqual(self.report()["count"], 0)
        self.assertEqual(self.report()["excluded"]["date"], 1)

    def test_title_once_not_each_installment_and_include_unpaid(self):
        raw = self.add()
        raw["payment_methods"][0]["parcels"].append({"uuid": "second", "status": "late"})
        self.conn.execute("update clinica_bills set raw_json=?", (json.dumps(raw),))
        self.conn.execute("insert into clinica_parcels values('second','bill','late','{}',10)")
        self.assertEqual((self.report()["count"], self.report()["totals"]["income"]), (1, 97))

    def test_income_expense_manual_receipt_and_negative_net(self):
        self.add()
        self.add(uuid="expense", kind="Conta", amount=6000, net=6000, status="paid")
        self.add(uuid="manual", kind="Conta", amount=4000, net=4000, status="received")
        report = self.report(direction="expense")
        self.assertEqual(report["totals"], {"income": 137, "expense": 60, "balance": 77})
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["items"][0]["net"], -60)
        self.assertEqual(report["items"][0]["gross"], 60)

    def test_open_expenses_and_manual_receivable(self):
        self.add(kind="Conta", status="open")
        self.add(uuid="income", kind="Conta", status="open", description="Título a receber de teste")
        self.assertEqual(self.report()["totals"], {"income": 97, "expense": 97, "balance": 0})

    def test_cancelled_initial_balance_and_unknown_direction(self):
        self.add(status="cancelled")
        self.add(uuid="balance", kind="Saldo Inicial")
        self.add(uuid="unknown", kind="Transferência")
        self.assertEqual(self.report()["count"], 0)
        self.assertEqual(self.report()["excluded"]["direction"], 1)

    def test_latest_parcel_status_controls_manual_receipt(self):
        self.add(kind="Conta", status="received")
        self.conn.execute("update clinica_parcels set status='paid'")
        self.assertEqual(self.report()["items"][0]["direction"], "expense")
        self.conn.execute("update clinica_bills set synced_at=12")
        self.assertEqual(self.report()["items"][0]["direction"], "income")

    def test_embedded_orphan_parent_deduplicated_and_missing_parent_reported(self):
        raw = self.add()
        self.conn.execute("delete from clinica_bills")
        self.conn.execute("update clinica_parcels set raw_json=?", (json.dumps({"raw_bill": raw}),))
        self.assertEqual(self.report()["count"], 1)
        self.conn.execute("insert into clinica_parcels values('orphan','unknown','paid','{}',1)")
        self.assertEqual(self.report()["excluded"]["date"], 1)

    def test_missing_net_and_explicit_fee_fallback_zero_retained(self):
        self.add(net=None)
        self.add(uuid="fees", net=None, fees_amount=300)
        self.add(uuid="zero", net=0)
        report = self.report()
        self.assertEqual(report["totals"]["income"], 97)
        self.assertEqual(report["excluded"]["amount"], 1)
        self.assertEqual(report["count"], 2)

    def test_filters_accent_insensitive_search_and_category_type_contact(self):
        self.add(description="Avaliação clínica", category={"name": "Consulta"})
        self.add(uuid="other", kind="Conta", category={"name": "Aluguel"})
        self.assertEqual(self.report(search="avaliacao")["count"], 1)
        self.assertEqual(self.report(contact="person-bill")["count"], 1)
        self.assertEqual(self.report(category="Aluguel", title_type="Venda")["count"], 0)
        self.assertEqual(len(self.report(category="Aluguel")["options"]["categories"]), 2)

    def test_professional_matching_is_exact_and_does_not_mix(self):
        self.add()
        self.add(uuid="direct", seller={"uuid": "doctor-b"})
        self.add(uuid="missing")
        self.conn.execute("insert into clinica_sales values('person-bill','2026-09-10','sale',?)",
                          (json.dumps({"final_amount": 10000, "seller": {"uuid": "doctor-a"}}),))
        a = build_report(self.conn, self.options, ["doctor-a"])
        b = build_report(self.conn, self.options, ["doctor-b"])
        self.assertEqual([row["uuid"] for row in a["items"]], ["bill"])
        self.assertEqual([row["uuid"] for row in b["items"]], ["direct"])
        self.assertEqual(a["excluded"]["professional"], 1)
        self.conn.execute("insert into clinica_sales values('person-bill','2026-09-10','sale',?)",
                          (json.dumps({"final_amount": 10000, "seller": {"uuid": "doctor-b"}}),))
        self.assertEqual(build_report(self.conn, self.options, ["doctor-a"])["count"], 0)

    def test_export_all_pages_totals_and_formula_safety(self):
        for number in range(61):
            self.add(uuid=str(number), description="=HYPERLINK(\"bad\")")
        self.assertEqual(len(self.report()["items"]), 50)
        self.assertEqual(len(self.report(page=2)["items"]), 11)
        self.assertEqual(self.report(page=999)["page"], 2)
        report = build_report(self.conn, self.options, export=True)
        workbook = load_workbook(io.BytesIO(export_workbook(report, self.options, "Clínica teste")))
        self.assertEqual(workbook["Competência"].max_row, 62)
        self.assertEqual(workbook["Competência"]["B2"].data_type, "s")
        self.assertEqual(sum(row[7] for row in workbook["Competência"].iter_rows(min_row=2, values_only=True)), 61 * 97)
        self.assertEqual(workbook["Resumo"]["B2"].value, 61 * 97)

    def test_invalid_dates_filters_sort_page_and_duplicate_parameters(self):
        for params in ({"date_from": ["2026-09-31"]}, {"date_from": ["2026-10-02"], "date_to": ["2026-10-01"]},
                       {"sort": ["raw_json"]}, {"direction": ["bad"]}, {"page": ["0"]},
                       {"category": ["a", "b"]}, {"search": ["a" * 501]}):
            with self.assertRaises(ValueError):
                query_options(params)


if __name__ == "__main__":
    unittest.main()
