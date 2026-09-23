import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
from access_policy import clinic_modules


class BrandaoClinicTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for patcher in (
            patch.object(app, "DATA_DIR", self.root),
            patch.object(app, "BASE_DIR", self.root),
            patch.object(app, "DB_PATH", self.root / "kommo_report.sqlite3"),
            patch.object(app, "LEGACY_DB_PATH", self.root / "kommo_report.sqlite3"),
            patch.dict(os.environ, {}, clear=True),
            patch("urllib.request.urlopen", side_effect=AssertionError("No external requests")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_new_clinic_has_own_database_and_preserves_existing_clinics(self):
        paths = set()
        for clinic in ("vielle", "inspire", "carla"):
            with app.clinic_context(clinic):
                paths.add(app.clinic_db_path())
                with app.db() as conn:
                    conn.execute("INSERT INTO pipelines(id,name,raw_json,synced_at) VALUES(1,?,'{}',0)", (clinic,))
        with app.clinic_context("brandao"):
            self.assertEqual(app.current_clinic_id(), "brandao")
            self.assertEqual(app.clinic_display_name(), "Clínica Brandão")
            self.assertEqual(app.clinic_db_path(), self.root / "kommo_report_brandao.sqlite3")
            self.assertNotIn(app.clinic_db_path(), paths)
            app.apply_kommo_reset_if_needed()
            with app.db() as conn:
                for table in ("pipelines", "leads", "clinica_patients", "clinica_sales", "patient_followup_contacts", "quote_followup_contacts"):
                    self.assertEqual(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        for clinic in ("vielle", "inspire", "carla"):
            with app.clinic_context(clinic), app.db() as conn:
                self.assertEqual(conn.execute("SELECT name FROM pipelines").fetchone()[0], clinic)

    def test_startup_does_not_reuse_global_integration_credentials(self):
        keys = ("KOMMO_SUBDOMAIN", "KOMMO_CLIENT_ID", "KOMMO_CLIENT_SECRET",
                "KOMMO_LONG_LIVED_TOKEN", "KOMMO_REDIRECT_URI", "CLINICA_EXPERTS_TOKEN",
                "META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "OPENAI_API_KEY")
        defaults = {key: "other-clinic-test-value" for key in keys}
        with patch.dict(app.CONFIG_DEFAULTS, defaults), patch.dict(os.environ, defaults):
            with app.clinic_context("brandao"):
                for key in keys:
                    self.assertEqual(app.config_value(key, ""), "", key)
                app.apply_kommo_reset_if_needed()
                for key in keys:
                    self.assertEqual(app.config_value(key, ""), "", key)
                self.assertIsNone(app.get_tokens())
                report = app.report_data(date_from="2026-09-01", date_to="2026-09-30")
                self.assertFalse(report["connected"])
                self.assertFalse(report["clinica_experts"]["connected"])
                self.assertEqual(report["clinica_experts"]["totals"]["sales_total"], 0)
                self.assertEqual(report["general_panel"]["receipts"]["net_total"], 0)
                self.assertEqual(report["filters"]["doctors"], [])

    def test_later_configuration_is_isolated_and_supported(self):
        with patch.dict(os.environ, {"BRANDAO_KOMMO_SUBDOMAIN": "brandao-test"}):
            with app.clinic_context("brandao"):
                self.assertEqual(app.config_value("KOMMO_SUBDOMAIN", ""), "brandao-test")
        with app.clinic_context("brandao"):
            app.apply_kommo_reset_if_needed()
            values = {"KOMMO_SUBDOMAIN": "brandao-test", "KOMMO_LONG_LIVED_TOKEN": "brandao-test-token",
                      "CLINICA_EXPERTS_TOKEN": "brandao-test-experts"}
            app.save_config_values(values)
            for key, value in values.items():
                self.assertEqual(app.config_value(key, ""), value)
        with app.clinic_context("vielle"), app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM app_settings").fetchone()[0], 0)
        self.assertIn("dashboard", clinic_modules("brandao"))
        self.assertIn("budget_followup", clinic_modules("brandao"))
        self.assertNotIn("patient_followup", clinic_modules("brandao"))
        self.assertNotIn("body_evolution", clinic_modules("brandao"))


if __name__ == "__main__":
    unittest.main()
