import importlib
import os
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from clinic_refresh import ClinicRefreshJobs
from periodic_sync import BRAZIL, ClinicSyncLocks, PeriodicClinicSync


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 1, 12, tzinfo=BRAZIL)
        self.saved, self.calls = {}, []
        self.jobs = ClinicRefreshJobs(cooldown=0)
        self.run = Mock(side_effect=lambda clinic, mode, progress: self.record(clinic, mode))
        self.scheduler = self.make()

    def record(self, clinic, mode):
        self.calls.append((clinic, mode))
        return [{"name": "Kommo", "status": "done"}]

    def make(self, **kwargs):
        return PeriodicClinicSync(("vielle", "inspire", "carla", "brandao"), self.jobs, self.run,
                                 lambda clinic: self.saved.get(clinic, {}),
                                 lambda clinic, state: self.saved.__setitem__(clinic, dict(state)),
                                 clock=lambda: self.now, **kwargs)

    def test_five_minutes_all_clinics_and_no_early_execution(self):
        self.scheduler.tick()
        self.now += timedelta(seconds=299)
        self.scheduler.tick()
        self.assertEqual(self.calls, [])
        self.now += timedelta(seconds=1)
        self.scheduler.tick()
        self.assertEqual(self.calls, [(key, "recent") for key in self.scheduler.clinics])
        self.scheduler.tick()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.jobs.status("inspire")["source"], "automatic_recent")

    def test_long_execution_schedules_next_cycle_after_completion(self):
        self.scheduler.tick()
        self.now += timedelta(minutes=5)
        def slow(clinic, mode, progress):
            self.now += timedelta(minutes=8)
            return self.record(clinic, mode)
        self.run.side_effect = slow
        self.scheduler.tick()
        first = self.scheduler.status("vielle")
        self.assertEqual(first["next_recent_at"] - first["recent_finished_at"], 300)

    def test_daily_at_three_brazil_once_and_restart_does_not_repeat(self):
        self.scheduler.tick()
        self.now = datetime(2026, 10, 2, 3, tzinfo=BRAZIL)
        self.scheduler.tick()
        self.assertEqual(self.calls, [(key, "daily") for key in self.scheduler.clinics])
        self.scheduler = self.make()
        self.scheduler.tick()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(datetime.fromtimestamp(self.scheduler.status("carla")["next_daily_at"], BRAZIL).hour, 3)

    def test_missed_daily_after_restart_is_reconciled(self):
        self.scheduler.tick()
        self.saved["vielle"] = {"next_daily_at": datetime(2026, 10, 1, 3, tzinfo=BRAZIL).timestamp()}
        self.scheduler = self.make()
        self.scheduler.tick()
        self.assertEqual(self.calls, [("vielle", "daily")])

    def test_manual_job_is_joined_without_duplicate_automatic_run(self):
        entered, release = threading.Event(), threading.Event()
        def manual(progress):
            entered.set()
            release.wait(2)
            return [{"name": "Kommo", "status": "done"}]
        self.jobs.start("vielle", manual)
        self.assertTrue(entered.wait(1))
        try:
            self.scheduler.tick()
            self.now += timedelta(minutes=5)
            self.scheduler.tick()
            self.assertNotIn(("vielle", "recent"), self.calls)
            self.assertEqual(len(self.calls), 3)
        finally:
            release.set()

    def test_errors_are_private_and_do_not_stop_other_clinics(self):
        self.scheduler.tick()
        self.now += timedelta(minutes=5)
        self.run.side_effect = [RuntimeError("private token"), [{"name": "Kommo", "status": "done"}], [], []]
        self.scheduler.tick()
        self.assertEqual(self.run.call_count, 4)
        self.assertEqual(self.jobs.status("vielle")["phase"], "error")
        self.assertNotIn("private token", str(self.saved))
        self.scheduler.tick()
        self.assertEqual(self.run.call_count, 4)

    def test_configuration_pause_resume_and_interval_change(self):
        options = {"enabled": False, "interval_seconds": 600}
        self.scheduler = self.make(settings_getter=lambda _: options)
        self.scheduler.tick()
        self.now += timedelta(minutes=10)
        self.scheduler.tick()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.scheduler.status("vielle")["enabled"])
        options.update(enabled=True, interval_seconds=300)
        self.scheduler.tick()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.scheduler.status("vielle")["interval_minutes"], 5)

    def test_concurrent_ticks_do_not_duplicate_work(self):
        self.scheduler.tick()
        self.now += timedelta(minutes=5)
        entered, release = threading.Event(), threading.Event()
        def blocked(clinic, mode, progress):
            entered.set()
            release.wait(2)
            return self.record(clinic, mode)
        self.run.side_effect = blocked
        thread = threading.Thread(target=self.scheduler.tick)
        thread.start()
        self.assertTrue(entered.wait(1))
        self.scheduler.tick()
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.calls), 4)

    def test_same_clinic_integration_entry_points_share_a_mutex(self):
        locks, entered, release = ClinicSyncLocks(), threading.Event(), threading.Event()
        events = []
        @locks.serialize(lambda: "inspire")
        def first():
            events.append("first-start")
            entered.set()
            release.wait(2)
            events.append("first-end")
        @locks.serialize(lambda: "inspire")
        def second():
            events.append("second")
        a, b = threading.Thread(target=first), threading.Thread(target=second)
        a.start()
        self.assertTrue(entered.wait(1))
        b.start()
        release.set()
        a.join(2)
        b.join(2)
        self.assertEqual(events, ["first-start", "first-end", "second"])


class PeriodicIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"DATA_DIR": self.temp.name}):
            self.app = importlib.import_module("app")
        for key, value in {"DATA_DIR": Path(self.temp.name), "DB_PATH": Path(self.temp.name) / "vielle.sqlite3",
                           "LEGACY_DB_PATH": Path(self.temp.name) / "missing.sqlite3"}.items():
            patcher = patch.object(self.app, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app.init_db()

    def test_recent_experts_does_not_reload_catalog_or_history(self):
        app = self.app
        with patch.object(app, "sync_clinica_list", side_effect=AssertionError("full catalog")), \
             patch.object(app, "sync_clinica_period", return_value=(1, 2, 3, 4, 5, [])) as period:
            result = app.sync_clinica_experts("2026-09-25", "2026-10-01", recent=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["patients"], 0)
        period.assert_called_once_with("2026-09-25", "2026-10-01", quote_date_from="2026-09-25", recent=True)

    def test_recent_financial_window_has_no_75_day_expansion(self):
        app = self.app
        with patch.object(app, "sync_clinica_list_variants", return_value=0) as sync:
            app.sync_clinica_bills_period("2026-09-25", "2026-10-01", recent=True)
            for path in sync.call_args.args[0]:
                self.assertIn("2026-09-25T00:00:00", path)
                self.assertIn("2026-10-01T23:59:59", path)
            self.assertTrue(sync.call_args.kwargs["strict"])
            app.sync_clinica_parcels_period("2026-09-25", "2026-10-01", recent=True)
            self.assertTrue(sync.call_args.kwargs["strict"])

    def test_all_failed_recent_variants_are_not_reported_as_success(self):
        with patch.object(self.app, "clinica_request", side_effect=RuntimeError("provider unavailable")):
            with self.assertRaises(RuntimeError):
                self.app.sync_clinica_list_variants(["/bills?a=1", "/bills?a=2"], ["bills"], Mock(), strict=True)

    def test_new_patient_seed_preserves_existing_patient_and_followup(self):
        app = self.app
        with app.db() as conn:
            app.save_clinica_patient(conn, {"uuid": "patient", "name": "Complete name", "phone": "123"}, 1)
            for uuid in ("patient", "new-patient"):
                app.save_clinica_booking(conn, {"uuid": "booking-" + uuid, "patient": {"uuid": uuid, "name": "Short name"}}, 100)
            self.assertEqual(app.seed_recent_clinica_patients(conn, 100), 1)
            existing = conn.execute("select name, phone from clinica_patients where uuid = 'patient'").fetchone()
            self.assertEqual(tuple(existing), ("Complete name", "123"))

    def seed_cursor(self):
        with self.app.db() as conn:
            conn.execute("insert into leads (id, name, raw_json, synced_at) values (1, 'Preserved', '{}', 1)")
            conn.execute("insert into app_settings (key, value, updated_at) values ('_KOMMO_INCREMENTAL_CURSOR', '1000', 1000)")

    def test_incremental_kommo_filters_and_advances_only_after_success(self):
        app = self.app
        self.seed_cursor()
        with patch.object(app.time, "time", return_value=2000), \
             patch.object(app, "get_access_context", return_value={"access_token": "demo", "account_domain": "demo"}), \
             patch.object(app, "sync_pipelines", return_value=1), \
             patch.object(app, "kommo_request", return_value={"_embedded": {"leads": [{"id": 2, "name": "New", "updated_at": 1900}]}}) as request, \
             patch.object(app, "sync_status_events", return_value=0) as events, \
             patch.object(app, "sync_lead_interaction_events", return_value=0):
            app.sync_leads(incremental=True)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[1]).query)
        self.assertEqual(query["filter[updated_at][from]"], ["880"])
        self.assertEqual(query["filter[updated_at][to]"], ["2000"])
        self.assertTrue(events.call_args.kwargs["fail_on_limit"])
        with app.db() as conn:
            self.assertEqual(conn.execute("select value from app_settings where key = '_KOMMO_INCREMENTAL_CURSOR'").fetchone()[0], "2000")
            self.assertEqual(conn.execute("select count(*) from leads").fetchone()[0], 2)

    def test_failed_incremental_kommo_does_not_advance_cursor(self):
        app = self.app
        self.seed_cursor()
        with patch.object(app, "get_access_context", return_value={"access_token": "demo", "account_domain": "demo"}), \
             patch.object(app, "sync_pipelines", return_value=1), \
             patch.object(app, "kommo_request", return_value={}), \
             patch.object(app, "sync_status_events", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                app.sync_leads(incremental=True)
        with app.db() as conn:
            self.assertEqual(conn.execute("select value from app_settings where key = '_KOMMO_INCREMENTAL_CURSOR'").fetchone()[0], "1000")

    def test_empty_kommo_base_bootstraps_without_incremental_filter(self):
        self.assertIsNone(self.app.kommo_incremental_since(2000))

    def test_schedule_state_is_persistent_and_scoped_to_clinic(self):
        self.app.periodic_sync_state("inspire", {"recent_finished_at": 1000})
        self.assertEqual(self.app.periodic_sync_state("inspire"), {"recent_finished_at": 1000})
        self.assertEqual(self.app.periodic_sync_state("vielle"), {})

    def test_periodic_window_and_daily_month_use_brazil_date(self):
        today = datetime(2026, 10, 1, 0, 5, tzinfo=BRAZIL)
        progress = Mock()
        with patch.object(self.app, "datetime") as clock, \
             patch.object(self.app, "refresh_clinic_integrations", return_value=[]) as refresh:
            clock.now.return_value = today
            self.app.run_periodic_refresh("inspire", "recent", progress)
            refresh.assert_called_once_with("inspire", "2026-09-25", "2026-10-01", progress, mode="recent")
            clock.now.assert_called_with(BRAZIL)
            refresh.reset_mock()
            self.app.run_periodic_refresh("inspire", "daily", progress)
            refresh.assert_called_once_with("inspire", "2026-10-01", "2026-10-01", progress, mode="daily")

    def test_recent_mode_routes_connected_services_without_historical_import(self):
        app = self.app
        values = {"KOMMO_LONG_LIVED_TOKEN": "demo", "CLINICA_EXPERTS_TOKEN": "demo"}
        with patch.object(app, "config_value", side_effect=lambda key, default="": values.get(key, default)), \
             patch.object(app, "sync_leads", return_value={"ok": True}) as kommo, \
             patch.object(app, "sync_clinica_experts", return_value={"ok": True}) as experts, \
             patch.object(app, "refresh_clinica_for_dashboard", side_effect=AssertionError("history")), \
             patch.object(app, "sync_paid_traffic", side_effect=AssertionError("not connected")):
            services = app.refresh_clinic_integrations("inspire", "2026-09-25", "2026-10-01", Mock(), mode="recent")
        kommo.assert_called_once_with(incremental=True)
        experts.assert_called_once_with(date_from="2026-09-25", date_to="2026-10-01", recent=True)
        self.assertEqual([item["status"] for item in services], ["done", "done", "skipped"])

    def test_settings_are_saved_per_clinic_and_interval_is_bounded(self):
        with self.app.clinic_context("inspire"):
            self.app.save_config_values({"AUTO_SYNC_ENABLED": "false", "AUTO_SYNC_INTERVAL_MINUTES": "2"})
        self.assertEqual(self.app.periodic_sync_settings("inspire"), {"enabled": False, "interval_seconds": 300})
        self.assertTrue(self.app.periodic_sync_settings("vielle")["enabled"])


if __name__ == "__main__":
    unittest.main()
