import contextlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from crm_patient_refresh import JOBS, patient_snapshot, start_refresh, sync_patients


def extract(payload, _):
    return payload if isinstance(payload, list) else payload.get("data", payload.get("patients", []))


class PatientRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SimpleNamespace(path=Path(self.tmp.name) / "crm.sqlite3", patient_directory=Mock())

    def test_complete_paginated_snapshot(self):
        first = [{"uuid": str(i), "name": "Paciente"} for i in range(100)]
        request = Mock(side_effect=[{"data": first}, {"patients": [{"uuid": "100", "name": "Ultimo"}]}])
        self.assertEqual(len(patient_snapshot(request, extract)), 101)
        self.assertEqual(request.call_args.args[0], "/patients?per_page=100&page=2")

    def test_empty_malformed_repeated_or_truncated_snapshot_is_not_accepted(self):
        full = [{"uuid": str(i), "name": "Paciente"} for i in range(100)]
        for responses, cap in (([[]], 100), ([{"error": "provider detail"}], 100),
                               ([[{"uuid": "p"}]], 100), ([full, full], 100), ([full], 1)):
            with self.subTest(responses=len(responses), cap=cap), self.assertRaises(ValueError):
                patient_snapshot(Mock(side_effect=responses), extract, cap)

    def test_sync_stages_before_writes_and_invalidates_only_after_commit(self):
        import app
        patient = {"uuid": "p", "name": "Paciente"}
        writes = Mock()
        with patch.object(app, "clinic_context", return_value=contextlib.nullcontext()), \
             patch.object(app, "config_value", return_value="configured-token"), \
             patch.object(app, "configured", return_value=True), \
             patch.object(app, "clinica_request", return_value=[patient]), \
             patch.object(app, "db", return_value=contextlib.nullcontext("connection")), \
             patch.object(app, "save_clinica_patient", writes):
            self.assertEqual(sync_patients(self.store, Mock())[0]["status"], "done")
        self.assertEqual(writes.call_args.args[:2], ("connection", patient))
        self.store.patient_directory.invalidate.assert_called_once()

    def test_failed_snapshot_preserves_cache_and_does_not_invalidate(self):
        import app
        with patch.object(app, "clinic_context", return_value=contextlib.nullcontext()), \
             patch.object(app, "config_value", return_value="configured-token"), \
             patch.object(app, "configured", return_value=True), \
             patch.object(app, "clinica_request", side_effect=RuntimeError("private provider error")), \
             patch.object(app, "db") as database:
            with self.assertRaises(RuntimeError): sync_patients(self.store, Mock())
        database.assert_not_called()
        self.store.patient_directory.invalidate.assert_not_called()

    def test_job_failure_is_sanitized_and_repeated_starts_are_deduplicated(self):
        from crm_patient_refresh import jobs_for
        jobs = jobs_for(self.store)
        job = jobs.start("vielle", lambda progress: (_ for _ in ()).throw(ValueError("private provider error")), background=False)
        self.assertEqual(job["phase"], "error")
        self.assertNotIn("private", str(job))
        with patch("crm_patient_refresh.sync_patients") as sync:
            self.assertFalse(start_refresh(self.store)["started"])
        sync.assert_not_called()
        JOBS.pop(self.store.path, None)

    def test_missing_directory_cannot_start_refresh(self):
        self.store.patient_directory = None
        with self.assertRaises(ValueError): start_refresh(self.store)


if __name__ == "__main__":
    unittest.main()
