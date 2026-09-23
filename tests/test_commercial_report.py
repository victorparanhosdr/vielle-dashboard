import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
from access_policy import project_report


class CommercialReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for patcher in (
            patch.object(app, "clinic_db_path", return_value=Path(temp.name) / "clinic.sqlite3"),
            patch.object(app, "clinic_pipeline_doctor_map", return_value={}),
            patch.object(app, "clinic_doctor_professionals", return_value={"Doutor A": "a", "Doutor B": "b"}),
            patch.object(app, "forced_professional_uuids", return_value=[]),
            patch("urllib.request.urlopen", side_effect=AssertionError("No external requests")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app.init_db()
        with app.db() as conn:
            conn.execute("insert into pipelines(id,name,raw_json,synced_at) values(1,'Teste','{}',0)")
            rows = [
                ("first", "sale", "active", "2026-09-01T00:00:00-03:00", "a", 10000),
                ("last", "sale", "active", "2026-09-30T23:59:59-03:00", "a", 20000),
                ("other", "sale", "active", "2026-09-15", "b", 30000),
                ("zero", "sale", "active", "2026-09-15", "a", 0),
                ("previous", "sale", "active", "2026-08-31", "a", 40000),
                ("next", "sale", "active", "2026-10-01", "a", 50000),
                ("quote", "sale_quote", "open", "2026-09-15", "a", 60000),
                ("won-quote", "sale_quote", "won", "2026-09-15", "a", 70000),
                ("inactive", "sale", "inactive", "2026-09-15", "a", 80000),
                ("cancelled", "sale", "cancelled", "2026-09-15", "a", 90000),
            ]
            for key, kind, status, day, seller, amount in rows:
                raw = {"uuid": key, "type": kind, "status": status, "sale_date": day,
                       "created_at": "2026-07-01T10:00:00-03:00", "final_amount": amount,
                       "seller": {"uuid": seller}, "buyer": {"uuid": key, "name": key},
                       "procedures": [{"name": "Procedimento Teste", "quantity": 1, "final_amount": amount}]}
                conn.execute("insert into clinica_sales(uuid,patient_uuid,type,sale_date,total,raw_json,synced_at) values(?,?,?,?,?,?,0)",
                             (key, key, kind, day, 9999, json.dumps(raw)))

    def report(self, **kwargs):
        return project_report(app.report_data(date_from="2026-09-01", date_to="2026-09-30", **kwargs), "commercial")

    def test_sales_cards_and_rankings_use_same_date_status_and_final_amount(self):
        report = self.report()
        totals = report["clinica_experts"]["totals"]
        self.assertEqual(totals["sales"], 4)
        self.assertEqual(totals["sales_total"], 600)
        cards = report["clinica_experts"]["doctor_cross"]
        self.assertEqual(sum(row["sales"] for row in cards), 4)
        self.assertEqual(sum(row["sales_total"] for row in cards), 600)
        intelligence = report["sales_intelligence"]
        self.assertEqual(sum(row["revenue"] for row in intelligence["performance_daily"]), 600)
        self.assertEqual(sum(row["amount"] for row in intelligence["top_patients"]), 600)
        self.assertEqual(sum(row["amount"] for row in intelligence["top_procedures"]), 600)
        self.assertEqual(sum(row["amount"] for row in intelligence["procedure_categories"]), 600)
        self.assertNotIn("financial", report)

    def test_doctor_filter_is_shared_with_rankings(self):
        report = self.report(doctor="Doutor A")
        self.assertEqual(report["clinica_experts"]["totals"]["sales_total"], 300)
        self.assertEqual(len(report["clinica_experts"]["doctor_cross"]), 1)
        self.assertEqual(sum(row["amount"] for row in report["sales_intelligence"]["top_patients"]), 300)

    def test_single_day_does_not_use_creation_date_or_adjacent_days(self):
        report = app.report_data(date_from="2026-09-30", date_to="2026-09-30")
        self.assertEqual(report["clinica_experts"]["totals"]["sales_total"], 200)
        self.assertEqual(report["clinica_experts"]["totals"]["sales"], 1)

    def test_general_panel_and_financial_keep_separate_bases(self):
        with app.db() as conn:
            conn.execute("insert into clinica_bills(uuid,type,due_date,amount,raw_json,synced_at) values('bill','sale','2026-09-02',900,'{}',0)")
            conn.execute("insert into clinica_parcels(uuid,type,status,due_date,amount,raw_json,synced_at) values('manual','other','received','2026-09-02',50,'{}',0)")
            conn.execute("insert into clinica_parcels(uuid,type,status,due_date,amount,category_name,raw_json,synced_at) values('expense','expense','paid','2026-09-02',30,'Aluguel','{}',0)")
            conn.execute("insert into clinica_parcels(uuid,type,status,due_date,amount,category_name,raw_json,synced_at) values('owner','expense','paid','2026-09-03',20,'Pró-labore','{}',0)")
        app.save_monthly_goal("2026-09", 1000)
        with patch.object(app, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 9, 15)
            report = app.report_data(date_from="2026-09-01", date_to="2026-09-30")
        panel = report["general_panel"]
        self.assertEqual(panel["revenue"], 600)
        self.assertEqual(panel["sales_count"], 4)
        self.assertEqual(panel["average_ticket"], 150)
        self.assertEqual(panel["active_revenue_days"], 3)
        self.assertEqual(panel["distinct_patients"], 4)
        self.assertEqual(panel["expenses_total"], 50)
        self.assertEqual(panel["balance"], 550)
        self.assertAlmostEqual(panel["margin_1_rate"], 570 / 600)
        self.assertAlmostEqual(panel["margin_2_rate"], 550 / 600)
        self.assertEqual(panel["goal_rate"], 0.6)
        self.assertEqual(panel["projected_revenue"], 1200)
        self.assertEqual(sum(row["income"] for row in panel["financial_daily"]), 600)
        self.assertEqual(sum(row["balance"] for row in panel["financial_daily"]), 550)
        self.assertEqual(sum(row["amount"] for row in panel["income_by_type"]), 600)
        self.assertEqual(sum(row["expenses"] for row in panel["expenses_daily"]), 50)
        daily = {row["day"]: row for row in panel["financial_daily"]}
        self.assertEqual(daily["2026-09-01"]["income"], 100)
        self.assertEqual(daily["2026-09-02"]["income"], 0)
        self.assertEqual(daily["2026-09-02"]["expenses"], 30)
        self.assertEqual(report["financial"]["totals"]["income"], 950)
        self.assertEqual(sum(row["income"] for row in report["financial"]["daily"]), 950)

    def test_general_panel_obeys_doctor_date_and_status_filters(self):
        for doctor, start, end, amount, count, days in (
            ("Doutor A", "2026-09-01", "2026-09-30", 300, 3, 2),
            ("Doutor B", "2026-09-01", "2026-09-30", 300, 1, 1),
            ("Doutor A", "2026-09-30", "2026-09-30", 200, 1, 1),
            ("Doutor A", "2026-09-15", "2026-09-15", 0, 1, 0),
            ("Doutor A", "2026-09-02", "2026-09-02", 0, 0, 0),
        ):
            with self.subTest(doctor=doctor, start=start, end=end):
                panel = app.report_data(date_from=start, date_to=end, doctor=doctor)["general_panel"]
                self.assertEqual(panel["revenue"], amount)
                self.assertEqual(panel["sales_count"], count)
                self.assertEqual(panel["active_revenue_days"], days)
                self.assertEqual(sum(row["income"] for row in panel["financial_daily"]), amount)
                self.assertEqual(sum(row["revenue"] for row in panel["sales_ticket_daily"]), amount)
                self.assertEqual(sum(row["amount"] for row in panel["value_ranges"]), amount)
                if not amount:
                    self.assertIsNone(panel["margin_1_rate"])
                    self.assertIsNone(panel["margin_2_rate"])

    def test_general_receipts_are_net_and_keep_the_professional_filter(self):
        with app.db() as conn:
            for key, seller, amount, net in (("first", "a", 10000, 9750), ("other", "b", 30000, 28500)):
                sale_day = "2026-09-01" if seller == "a" else "2026-09-15"
                app.save_clinica_bill(conn, {
                    "uuid": "bill-" + key, "type": "Venda", "emission_date": sale_day,
                    "person": {"uuid": key}, "final_amount": amount,
                    "payment_methods": [{"parcels": [{"uuid": "parcel-" + key,
                        "status": "received", "final_amount": amount, "net_amount": net,
                        "fees_amount": amount - net, "compensation_date": "2026-09-20"}]}],
                }, 100)
        for doctor, sold, received in (("Doutor A", 300, 97.5), ("Doutor B", 300, 285), (None, 600, 382.5)):
            with self.subTest(doctor=doctor):
                panel = app.report_data(date_from="2026-09-01", date_to="2026-09-30", doctor=doctor)["general_panel"]
                self.assertEqual(panel["revenue"], sold)
                self.assertEqual(panel["receipts"]["net_total"], received)
        panel = app.report_data(date_from="2026-09-01", date_to="2026-09-15", doctor="Doutor A")["general_panel"]
        self.assertEqual(panel["receipts"]["net_total"], 0)
        with patch.object(app, "forced_professional_uuids", return_value=["b"]):
            panel = app.report_data(date_from="2026-09-01", date_to="2026-09-30", doctor="Doutor A")["general_panel"]
            self.assertEqual(panel["receipts"]["net_total"], 0)


if __name__ == "__main__":
    unittest.main()
