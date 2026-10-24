"""Synthetic scheduling/journal checks; injected providers never contact Google."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import unittest
from unittest.mock import Mock

from offsite_backup import BackupError, check_restore, digest_file
from offsite_backup_service import exclusive_job, run_once, secure_directory


class FakeDrive:
    def __init__(self, token, owner):
        self.identities, self.uploads = 0, []
        self.counter, self.failure = 0, False

    def identity(self):
        self.identities += 1

    def new_id(self):
        self.counter += 1
        return ("fixture" + str(self.counter)) * 3

    def folder(self, ident):
        return {"id": ident}

    def upload(self, packet, folder, ident, expected, checkpoint, *, session):
        self.uploads.append((ident, session, packet.read_bytes()))
        checkpoint({"session": "fixture-session", "offset": 0, "verified": False})
        if self.failure:
            raise BackupError("google_response_uncertain")
        checkpoint({"session": "", "offset": expected["size"], "verified": True})
        return {"id": ident, "verified": True, "size": expected["size"], "sha256": expected["sha256"]}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(os.environ["BACKUP_TEST_ROOT"]) / "service-case"
        self.work.mkdir()
        self.env = {"DATA_DIR": str(self.work), "DOC4DOCS_OFFSITE_BACKUP_ENABLED": "1",
                    "DOC4DOCS_OFFSITE_RECIPIENT": "age1" + "q" * 58,
                    "DOC4DOCS_OFFSITE_GOOGLE_OWNER": "owner@example.invalid",
                    "DOC4DOCS_OFFSITE_GOOGLE_ACCOUNT_ID": "a" * 32}
        self.drive = FakeDrive(None, None)
        self.snapshot = Mock(side_effect=self.make_packet)
        self.workspace = self.work / ".offsite-backup"

    def tearDown(self):
        shutil.rmtree(self.work)

    def make_packet(self, source, packet, scratch, recipient, **kwargs):
        packet.write_bytes(b"age-encryption.org/v1\nsynthetic-only\n")
        return digest_file(packet) | {"format": "fixture", "databases": 1, "files": 1}

    def run_job(self):
        return run_once(self.env, token_factory=lambda *args: object(),
                        drive_factory=lambda *args: self.drive, snapshot=self.snapshot)

    def test_disabled_has_no_filesystem_provider_or_snapshot_side_effects(self):
        self.env["DOC4DOCS_OFFSITE_BACKUP_ENABLED"] = "0"
        self.assertEqual(self.run_job(), {"status": "disabled"})
        self.assertFalse(self.workspace.exists())
        self.snapshot.assert_not_called()
        self.assertEqual(self.drive.identities, 0)

    def test_success_has_receipt_and_does_not_run_again_before_interval(self):
        self.assertEqual(self.run_job()["status"], "uploaded_checksum_verified")
        self.assertEqual(self.run_job()["status"], "not_due")
        self.assertEqual(self.snapshot.call_count, 1)
        self.assertEqual(len(self.drive.uploads), 1)
        self.assertEqual(len(list((self.workspace / "receipts").glob("*.json"))), 1)
        state = json.loads((self.workspace / "state.json").read_text())
        self.assertIsNone(state["active"])
        self.assertGreater(state["last_uploaded"], 0)

    def test_uncertain_upload_preserves_ciphertext_and_reuses_id_without_reexport(self):
        self.drive.failure = True
        with self.assertRaisesRegex(BackupError, "uncertain"):
            self.run_job()
        state = json.loads((self.workspace / "state.json").read_text())
        self.assertEqual(state["active"]["phase"], "prepared")
        self.assertEqual(state["active"]["session"], "fixture-session")
        self.drive.failure = False
        self.run_job()
        self.assertEqual(self.snapshot.call_count, 1)
        self.assertEqual(self.drive.uploads[0][0], self.drive.uploads[1][0])
        self.assertEqual(self.drive.uploads[1][1], "fixture-session")

    def test_configuration_change_does_not_silently_switch_account(self):
        self.run_job()
        self.env["DOC4DOCS_OFFSITE_GOOGLE_OWNER"] = "other@example.invalid"
        with self.assertRaisesRegex(BackupError, "configuration_changed"):
            self.run_job()
        self.assertEqual(len(self.drive.uploads), 1)

    def test_second_job_cannot_run_while_first_holds_lock(self):
        secure_directory(self.workspace)
        with exclusive_job(self.workspace):
            with self.assertRaisesRegex(BackupError, "already_running"):
                self.run_job()
        self.assertEqual(self.drive.identities, 0)

    def test_service_real_encryption_and_recovery_with_mocked_drive(self):
        identity = self.work / "dummy.key"
        subprocess.run([os.environ["BACKUP_TEST_KEYGEN"], "-o", str(identity)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        public = subprocess.run([os.environ["BACKUP_TEST_KEYGEN"], "-y", str(identity)],
                                check=True, capture_output=True, text=True).stdout.strip()
        source = self.work / "volume"
        source.mkdir()
        with sqlite3.connect(source / "auth.sqlite3") as connection:
            connection.execute("CREATE TABLE fixture(value TEXT)")
            connection.execute("INSERT INTO fixture VALUES('synthetic end-to-end')")
        env = self.env | {"DATA_DIR": str(source), "DOC4DOCS_OFFSITE_RECIPIENT": public,
                          "DOC4DOCS_OFFSITE_AGE_BIN": os.environ["BACKUP_TEST_AGE"],
                          "DOC4DOCS_OFFSITE_MAX_BYTES": str(64 * 1024**2)}
        result = run_once(env, token_factory=lambda *args: object(), drive_factory=lambda *args: self.drive)
        self.assertEqual(result["status"], "uploaded_checksum_verified")
        packet = self.work / "downloaded.age"
        packet.write_bytes(self.drive.uploads[0][2])
        self.assertNotIn(b"synthetic end-to-end", packet.read_bytes())
        restored = self.work / "recovery"
        self.assertTrue(check_restore(packet, identity, restored, age=os.environ["BACKUP_TEST_AGE"])["ok"])
        with sqlite3.connect(restored / "data/auth.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT value FROM fixture").fetchone()[0], "synthetic end-to-end")
        self.assertFalse(list((source / ".offsite-backup/jobs").glob("*/packet.age")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
