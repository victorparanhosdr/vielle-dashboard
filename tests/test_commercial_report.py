import json
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


if __name__ == "__main__":
    unittest.main()
