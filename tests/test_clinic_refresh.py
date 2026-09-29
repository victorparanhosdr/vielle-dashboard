import contextlib
import importlib
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from clinic_refresh import ClinicRefreshJobs


class RefreshJobTests(unittest.TestCase):
    def wait_done(self, jobs, clinic):
        deadline = time.monotonic() + 3
        while jobs.status(clinic)["running"] and time.monotonic() < deadline:
            time.sleep(0.005)
        result = jobs.status(clinic)
        self.assertFalse(result["running"])
        return result

    def test_deduplicates_per_clinic_and_applies_cooldown(self):
        jobs = ClinicRefreshJobs()
        release, entered = threading.Event(), threading.Event()
        def run(progress):
            progress("Kommo")
            entered.set()
            release.wait(3)
            return [{"name": "Kommo", "status": "done"}]
        first = jobs.start("vielle", run)
        self.assertTrue(entered.wait(1))
        try:
            self.assertTrue(first["started"])
            self.assertEqual(jobs.status("vielle")["message"], "Atualizando Kommo...")
            second = jobs.start("vielle", Mock(side_effect=AssertionError("duplicate")))
            self.assertFalse(second["started"])
            self.assertEqual(first["job_id"], second["job_id"])
            self.assertTrue(jobs.start("inspire", lambda _: [{"name": "Kommo", "status": "skipped"}])["started"])
            self.assertEqual(self.wait_done(jobs, "inspire")["phase"], "empty")
        finally:
            release.set()
        done = self.wait_done(jobs, "vielle")
        self.assertEqual(done["phase"], "done")
        self.assertGreater(done["retry_after"], 0)
        self.assertFalse(jobs.start("vielle", run)["started"])
        done["services"].clear()
        self.assertEqual(len(jobs.status("vielle")["services"]), 1)

    def test_partial_failure_and_private_errors(self):
        jobs = ClinicRefreshJobs(cooldown=0)
        jobs.start("vielle", lambda _: [{"name": "Kommo", "status": "error"},
                                      {"name": "Clínica Experts", "status": "warning"}])
        self.assertEqual(self.wait_done(jobs, "vielle")["phase"], "partial")
        jobs.start("vielle", Mock(side_effect=RuntimeError("secret provider payload")))
        result = self.wait_done(jobs, "vielle")
        self.assertEqual(result["phase"], "error")
        self.assertNotIn("secret provider payload", str(result))

    def test_thread_failure_can_be_retried(self):
        jobs = ClinicRefreshJobs()
        with patch("clinic_refresh.threading.Thread.start", side_effect=RuntimeError("start failed")):
            with self.assertRaises(RuntimeError):
                jobs.start("vielle", Mock())
        self.assertFalse(jobs.status("vielle")["running"])
        self.assertEqual(jobs.status("vielle")["retry_after"], 0)


class RefreshIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"DATA_DIR": cls.temp.name}):
            cls.app = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_connected_integrations_continue_after_one_failure(self):
        app = self.app
        progress = Mock()
        with patch.object(app, "clinic_context", return_value=contextlib.nullcontext()) as context, \
             patch.object(app, "config_value", return_value="configured-demo"), \
             patch.object(app, "sync_leads", side_effect=RuntimeError("private Kommo response")) as kommo, \
             patch.object(app, "refresh_clinica_for_dashboard", return_value={"ok": True, "warnings": True}) as experts, \
             patch.object(app, "sync_paid_traffic", return_value={"ok": True, "account": "private"}) as meta:
            result = app.refresh_clinic_integrations("brandao", "2026-09-01", "2026-09-30", progress)
        context.assert_called_once_with("brandao")
        kommo.assert_called_once_with()
        experts.assert_called_once_with("brandao")
        meta.assert_called_once_with(date_from="2026-09-01", date_to="2026-09-30")
        self.assertEqual(result, [{"name": "Kommo", "status": "error"},
                                 {"name": "Clínica Experts", "status": "warning"},
                                 {"name": "Meta Ads", "status": "done"}])

    def test_missing_connections_are_skipped_without_syncing(self):
        app = self.app
        with patch.object(app, "clinic_context", return_value=contextlib.nullcontext()), \
             patch.object(app, "config_value", return_value=""), patch.object(app, "get_tokens", return_value=None), \
             patch.object(app, "sync_leads") as kommo, patch.object(app, "refresh_clinica_for_dashboard") as experts, \
             patch.object(app, "sync_paid_traffic") as meta:
            result = app.refresh_clinic_integrations("inspire", "2026-09-01", "2026-09-30", Mock())
        self.assertEqual([row["status"] for row in result], ["skipped"] * 3)
        for service in (kommo, experts, meta):
            service.assert_not_called()

    def test_clinica_join_uses_sanitized_existing_state(self):
        app = self.app
        state = {"inspire": {"running": True}}
        def complete(_):
            state["inspire"] = {"running": False, "ok": True, "warnings": True, "message": "private details"}
        with patch.object(app, "CLINICA_BACKGROUND_SYNC_STATE", state), \
             patch.object(app, "start_clinica_background_sync", return_value={"started": False}) as start, \
             patch.object(app.time, "sleep", side_effect=complete):
            result = app.refresh_clinica_for_dashboard("inspire")
        self.assertEqual(result, {"ok": True, "warnings": True})
        start.assert_called_once_with("inspire")

    def test_period_validation(self):
        app = self.app
        with patch.object(app, "default_period", return_value=("2026-09-01", "2026-09-30")):
            self.assertEqual(app.dashboard_refresh_period({}), ("2026-09-01", "2026-09-30"))
        for value in ([], None, {"reset_data": False}, {"date_from": "20260901", "date_to": "2026-09-30"},
                      {"date_from": 3, "date_to": "2026-09-30"}):
            with self.assertRaises(ValueError):
                app.dashboard_refresh_period(value)


if __name__ == "__main__":
    unittest.main()
