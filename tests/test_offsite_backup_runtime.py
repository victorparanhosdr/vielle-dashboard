"""Synthetic scheduler cases; no application imports or external providers."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch

from offsite_backup import BackupError
import offsite_backup_runtime as runtime
from offsite_backup_service import atomic_json, read_state, secure_directory


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ["BACKUP_TEST_ROOT"]) / "runtime-case"
        self.root.mkdir()
        self.workspace = self.root / ".offsite-backup"
        self.env = {"DOC4DOCS_OFFSITE_BACKUP_ENABLED": "1"}
        self.now = datetime(2026, 10, 24, 3, tzinfo=runtime.ZONE).timestamp()
        self.runner = Mock(return_value={"status": "uploaded_checksum_verified"})

    def tearDown(self):
        shutil.rmtree(self.root)

    def state(self, last=0, active=None):
        secure_directory(self.workspace)
        atomic_json(self.workspace / "state.json", {
            "version": 1, "folder_id": "f" * 20, "active": active, "last_uploaded": last})

    def cycle(self, now=None):
        return runtime.cycle(self.root, self.env, runner=self.runner, now=self.now if now is None else now)

    def test_disabled_has_no_filesystem_or_runner_effects(self):
        self.assertEqual(runtime.cycle(self.root, {}, runner=self.runner)["status"], "disabled")
        self.assertFalse(self.workspace.exists())
        self.runner.assert_not_called()

    def test_first_execution_records_success_and_next_slot(self):
        self.assertEqual(self.cycle()["status"], "uploaded_checksum_verified")
        current = runtime.health(self.workspace)
        self.assertEqual(current["status"], "uploaded")
        self.assertEqual(current["retry_at"], self.now + 86400)
        self.assertEqual(self.runner.call_args.args[0]["DATA_DIR"], str(self.root))

    def test_before_daily_slot_does_not_execute(self):
        self.state(self.now - 86400 + 60)
        self.assertEqual(self.cycle(self.now - 1)["status"], "waiting")
        self.runner.assert_not_called()

    def test_missed_slot_after_restart_is_caught_up(self):
        self.state(self.now - 86400 + 60)
        self.cycle(self.now + 4 * 3600)
        self.runner.assert_called_once()

    def test_backoff_then_review_after_six_failures(self):
        self.runner.side_effect = BackupError("google_unavailable")
        now = self.now
        for attempt in range(1, 7):
            result = self.cycle(now)
            current = runtime.health(self.workspace)
            self.assertEqual(current["failures"], attempt)
            if attempt < 6:
                self.assertEqual(result["status"], "failed")
                self.assertEqual(self.cycle(now + 1)["status"], "waiting")
                now = current["retry_at"]
            else:
                self.assertEqual(result["status"], "review")
        count = self.runner.call_count
        self.assertEqual(self.cycle(now + 86400)["status"], "review")
        self.assertEqual(self.runner.call_count, count)

    def test_explicit_review_preserves_active_upload_identifiers(self):
        active = {"job_id": "a" * 32, "file_id": "b" * 20, "attempts": 6,
                  "phase": "prepared", "session": "synthetic-session", "offset": 12}
        self.state(active=active)
        self.runner.side_effect = BackupError("fixture_failure")
        self.cycle()
        runtime.request_retry(self.root, self.env)
        self.cycle(self.now + 1)
        after = read_state(self.workspace / "state.json")["active"]
        self.assertEqual(after, active | {"attempts": 0})
        self.assertFalse((self.workspace / "request.json").exists())

    def test_disabled_retry_rejected(self):
        with self.assertRaisesRegex(BackupError, "backup_disabled"):
            runtime.request_retry(self.root, {})
        self.assertFalse(self.workspace.exists())

    def test_error_details_are_not_exposed(self):
        self.runner.side_effect = BackupError("unsafe details fixture@example.invalid")
        self.cycle()
        self.assertEqual(runtime.overview(self.root, self.env)["error"], "backup_failed")

    def test_overview_has_no_implicit_restore_success(self):
        self.state(self.now - 3 * 86400)
        with patch.object(runtime.time, "time", return_value=self.now):
            view = runtime.overview(self.root, self.env)
        self.assertTrue(view["needs_attention"])
        self.assertFalse(view["restore_verified"])
        self.assertEqual(view["retention"], "all")

    def test_invalid_schedule_never_executes(self):
        self.env["DOC4DOCS_OFFSITE_BACKUP_HOUR"] = "24"
        with self.assertRaisesRegex(BackupError, "invalid_backup_schedule"):
            self.cycle()
        self.runner.assert_not_called()

    def test_suspended_review_has_no_automatic_next_attempt(self):
        self.runner.side_effect = BackupError("google_unavailable")
        self.cycle()
        current = runtime.health(self.workspace)
        current.update(status="review", failures=6, retry_at=0)
        atomic_json(self.workspace / "health.json", current)
        self.assertEqual(runtime.overview(self.root, self.env)["next_attempt"], 0)

    def test_in_progress_request_does_not_enqueue_another_job(self):
        self.cycle()
        current = runtime.health(self.workspace)
        current["status"] = "running"
        atomic_json(self.workspace / "health.json", current)
        self.assertEqual(runtime.request_retry(self.root, self.env)["status"], "running")
        self.assertFalse((self.workspace / "request.json").exists())


if __name__ == "__main__":
    unittest.main()
