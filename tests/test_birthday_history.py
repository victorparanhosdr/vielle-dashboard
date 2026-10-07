from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import app
import birthday_history as history
from tests.test_patient_birthdays import seed, sale


class BirthdayHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        patcher = patch.object(app, "clinic_db_path", side_effect=lambda: root / (app.current_clinic_id() + ".sqlite3"))
        patcher.start(); self.addCleanup(patcher.stop)
        with app.clinic_context("vielle"), app.db() as conn:
            seed(conn)
            sale(conn, "existing")

    def api(self, path):
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if parsed.path == "/patients":
            return {"data": [{"uuid": "patient", "name": "Ana Costa", "date_birth": "1985-10-07"}]}
        day = query["starts_at"][0][:10]
        return {"data": [{"uuid": day, "sale_date": day, "type": "sale", "status": "active",
                          "final_amount": 10000, "buyer": {"uuid": "patient"}}]}

    def test_monthly_2024_through_current_year_upserts_and_clinic_isolation(self):
        end = date(2026, 10, 7)
        with patch.object(app, "clinica_request", side_effect=self.api) as request:
            history.run("vielle", lambda _: None, end)
        calls = [call.args[0] for call in request.call_args_list if call.args[0].startswith("/sales")]
        self.assertEqual(len(calls), 34 * 3)
        self.assertTrue(any("2024-01-01T" in path for path in calls))
        self.assertTrue(any("2025-01-01T" in path for path in calls))
        with app.clinic_context("vielle"), app.db() as conn, patch.object(app, "config_value", return_value="token"):
            self.assertTrue(history.status(conn, "vielle", end)["complete"])
            self.assertEqual(conn.execute("SELECT count(*) FROM clinica_sales").fetchone()[0], 35)
            self.assertEqual(conn.execute("SELECT name FROM clinica_patients WHERE uuid='patient'").fetchone()[0], "Ana Costa")
        with app.clinic_context("inspire"), app.db() as conn:
            self.assertEqual(history.status(conn, "inspire", end)["completed_months"], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM clinica_sales").fetchone()[0], 0)

    def test_failed_month_is_not_checkpointed_and_retry_resumes(self):
        def interrupted(path):
            if "starts_at=2024-02-01" in path:
                raise RuntimeError("Unavailable")
            return self.api(path)
        with patch.object(app, "clinica_request", side_effect=interrupted), self.assertRaises(RuntimeError):
            history.run("vielle", lambda _: None, date(2024, 3, 7))
        with app.clinic_context("vielle"), app.db() as conn:
            self.assertEqual(history.status(conn, "vielle", date(2024, 3, 7))["completed_months"], 1)
        with patch.object(app, "clinica_request", side_effect=self.api) as request:
            history.run("vielle", lambda _: None, date(2024, 3, 7))
        self.assertFalse(any("starts_at=2024-01" in c.args[0] for c in request.call_args_list))
        with app.clinic_context("vielle"), app.db() as conn:
            self.assertTrue(history.status(conn, "vielle", date(2024, 3, 7))["complete"])

    def test_pagination_metadata_short_pages_and_repeated_pages(self):
        def pages(path):
            number = int(parse_qs(urlsplit(path).query)["page"][0])
            return {"data": [{"uuid": f"p{number}", "name": "Patient"}], "meta": {"last_page": 2}}
        with app.clinic_context("vielle"), patch.object(app, "clinica_request", side_effect=pages):
            self.assertEqual(history.fetch_pages("/patients?per_page=100", ["data"], app.save_clinica_patient), 2)
        duplicate = {"data": [{"uuid": "repeat", "name": "Patient"}], "meta": {"last_page": 2}}
        with app.clinic_context("vielle"), patch.object(app, "clinica_request", return_value=duplicate), self.assertRaises(RuntimeError):
            history.fetch_pages("/patients", ["data"], app.save_clinica_patient)

    def test_unexpected_data_and_page_limit_are_not_silently_accepted(self):
        for payload in ({"error": "No access"}, {"data": [{}]}, {"data": [], "meta": {"last_page": 2}},
                        {"data": [{"uuid": "a"}], "meta": {"last_page": 0}},
                        {"data": [{"uuid": str(n)} for n in range(100)]}):
            with app.clinic_context("vielle"), patch.object(app, "clinica_request", return_value=payload), self.assertRaises(RuntimeError):
                history.fetch_pages("/patients", ["data"], app.save_clinica_patient, max_pages=1)


if __name__ == "__main__":
    unittest.main()
