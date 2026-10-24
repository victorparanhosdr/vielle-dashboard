"""Synthetic-only tests; invoke with tools/run_doc4docs_backup_tests.py."""
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import unittest
from unittest.mock import patch

import offsite_backup as backup

ROOT = Path(os.environ["BACKUP_TEST_ROOT"])
AGE = os.environ["BACKUP_TEST_AGE"]
KEYGEN = os.environ["BACKUP_TEST_KEYGEN"]
MEDIA = "a" * 32


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / "case"
        self.work.mkdir()
        self.source = self.work / "source"
        self.source.mkdir()
        self.identity = self.work / "identity.key"
        subprocess.run([KEYGEN, "-o", str(self.identity)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        public = subprocess.run([KEYGEN, "-y", str(self.identity)], check=True, capture_output=True, text=True)
        self.recipient = public.stdout.strip()
        self.database = self.source / "auth.sqlite3"
        with sqlite3.connect(self.database) as c:
            c.execute("CREATE TABLE fixture(id INTEGER PRIMARY KEY,value TEXT)")
            c.execute("INSERT INTO fixture VALUES(1,'synthetic-only')")
        self.encrypted = self.work / "backup.age"
        self.stage = self.work / "stage"
        self.restore = self.work / "restore"
        self.limits = backup.Limits(files=50, bytes=512 * 1024, seconds=10, databases=4)

    def tearDown(self):
        shutil.rmtree(self.work)

    def prepare(self, **kwargs):
        return backup.prepare(self.source, self.encrypted, self.stage, self.recipient,
                              age=AGE, limits=self.limits, **kwargs)

    def recover(self, identity=None):
        return backup.check_restore(self.encrypted, identity or self.identity, self.restore,
                                    age=AGE, limits=self.limits)

    def test_round_trip_and_wal(self):
        with sqlite3.connect(self.database) as live:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("INSERT INTO fixture VALUES(2,'committed-in-wal')")
            live.commit()
            (self.source / "asset.bin").write_bytes(b"synthetic attachment")
            result = self.prepare()
            self.assertEqual(result["databases"], 1)
            self.assertFalse(self.stage.exists())
            self.assertNotIn(b"synthetic attachment", self.encrypted.read_bytes())
            self.assertTrue(self.recover()["ok"])
            with sqlite3.connect(self.restore / "data" / "auth.sqlite3") as restored:
                self.assertEqual(restored.execute("SELECT count(*) FROM fixture").fetchone()[0], 2)
            self.assertEqual(live.execute("SELECT count(*) FROM fixture").fetchone()[0], 2)
            self.assertEqual((self.restore / "data" / "asset.bin").read_bytes(), b"synthetic attachment")
            self.assertFalse((self.restore / "data" / "auth.sqlite3-wal").exists())

    def test_existing_destination_preserved(self):
        self.encrypted.write_bytes(b"existing unrelated file")
        with self.assertRaises(backup.BackupError):
            self.prepare()
        self.assertEqual(self.encrypted.read_bytes(), b"existing unrelated file")

    def test_low_disk_space_rejects_before_creating_output(self):
        with patch.object(backup.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(1024, 1024, 0)):
            with self.assertRaisesRegex(backup.BackupError, "insufficient_space"):
                self.prepare()
        self.assertFalse(self.encrypted.exists())
        self.assertFalse(self.stage.exists())

    def test_production_budget_keeps_full_disk_reserve(self):
        with patch.object(backup.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(128 * backup.BLOCK, 0, 128 * backup.BLOCK)):
            with self.assertRaisesRegex(backup.BackupError, "insufficient_space"):
                backup.prepare(self.source, self.encrypted, self.stage, self.recipient, age=AGE)
        self.assertFalse(self.encrypted.exists())

    def test_output_race_preserves_other_file(self):
        original = backup.sqlite_snapshot
        def race(*args):
            original(*args)
            self.encrypted.write_bytes(b"created by another process")
        with patch.object(backup, "sqlite_snapshot", race), self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(self.encrypted.read_bytes(), b"created by another process")

    def test_corrupt_database_fails_closed(self):
        self.database.write_bytes(b"not a SQLite database")
        with self.assertRaises(backup.BackupError):
            self.prepare()
        self.assertFalse(self.encrypted.exists())
        self.assertFalse(self.stage.exists())

    def test_source_symlink_rejected(self):
        (self.source / "asset.bin").symlink_to(self.identity)
        with self.assertRaises(backup.BackupError):
            self.prepare()
        self.assertFalse(self.encrypted.exists())

    def test_missing_decrypt_binary_cleans_only_new_destination(self):
        self.prepare()
        with self.assertRaises(OSError):
            backup.check_restore(self.encrypted, self.identity, self.restore,
                                 age=str(self.work / "absent-age"), limits=self.limits)
        self.assertFalse(self.restore.exists())
        self.restore.mkdir()
        with self.assertRaises(FileExistsError):
            self.recover()
        self.assertTrue(self.restore.is_dir())

    def test_restore_file_count_limit(self):
        (self.source / "asset.bin").write_bytes(b"synthetic attachment")
        self.prepare()
        with self.assertRaisesRegex(backup.BackupError, "restore_limit"):
            backup.check_restore(self.encrypted, self.identity, self.restore,
                                 age=AGE, limits=backup.Limits(files=1, bytes=512 * 1024))
        self.assertFalse(self.restore.exists())

    def test_wrong_key_and_tampering(self):
        self.prepare()
        other = self.work / "other.key"
        subprocess.run([KEYGEN, "-o", str(other)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.assertRaises(Exception):
            self.recover(other)
        self.assertFalse(self.restore.exists())
        content = self.encrypted.read_bytes()
        self.encrypted.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
        with self.assertRaises(Exception):
            self.recover()
        self.assertFalse(self.restore.exists())

    def test_source_change_not_reported_as_success(self):
        asset = self.source / "asset.bin"
        asset.write_bytes(b"before")
        original = backup.inventory
        def changed(*args):
            result = original(*args)
            asset.write_bytes(b"changed after inventory")
            return result
        with patch.object(backup, "inventory", changed), self.assertRaises(backup.BackupError):
            self.prepare()
        self.assertFalse(self.encrypted.exists())

    def test_missing_media_not_reported_as_complete(self):
        folder = self.source / "crm" / "vielle"
        folder.mkdir(parents=True)
        with sqlite3.connect(folder / "crm.sqlite3") as c:
            c.execute("CREATE TABLE message_media(id TEXT,size INTEGER,sha256 TEXT,download_state TEXT,followup_asset_id TEXT)")
            c.execute("INSERT INTO message_media VALUES(?,3,?,'ready','')", (MEDIA, "b" * 64))
        with self.assertRaisesRegex(backup.BackupError, "missing_reference"):
            self.prepare()
        self.assertFalse(self.encrypted.exists())

    def test_foreign_key_violation_fails(self):
        with sqlite3.connect(self.database) as c:
            c.execute("CREATE TABLE broken(id INTEGER REFERENCES fixture(id))")
            c.execute("INSERT INTO broken VALUES(99)")
        with self.assertRaisesRegex(backup.BackupError, "sqlite_foreign_keys"):
            self.prepare()
        self.assertFalse(self.encrypted.exists())

    def test_no_self_capture_and_size_bound(self):
        with self.assertRaises(backup.BackupError):
            backup.prepare(self.source, self.source / "backup.age", self.stage, self.recipient, age=AGE)
        (self.source / "asset.bin").write_bytes(b"x" * 1024)
        with self.assertRaises(backup.BackupError):
            backup.prepare(self.source, self.encrypted, self.stage, self.recipient,
                           age=AGE, limits=backup.Limits(bytes=100))

    def test_restore_rejects_traversal(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            item = tarfile.TarInfo("../escape.txt")
            item.size = 3
            archive.addfile(item, io.BytesIO(b"bad"))
        with self.encrypted.open("xb") as out:
            subprocess.run([AGE, "-r", self.recipient], input=stream.getvalue(), stdout=out,
                           stderr=subprocess.DEVNULL, check=True)
        with self.assertRaises(backup.BackupError):
            self.recover()
        self.assertFalse((self.work / "escape.txt").exists())
        self.assertFalse(self.restore.exists())

    def test_sandbox_blocks_outside_files_and_network(self):
        with self.assertRaises(OSError):
            Path("/outside-scratch.txt").write_bytes(b"blocked")
        with self.assertRaises(OSError):
            Path(__file__).write_bytes(b"blocked")
        with self.assertRaises(OSError):
            Path("/credentials/must-not-exist").read_bytes()
        import socket
        try:
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except PermissionError:
            return
        with connection:
            connection.settimeout(1)
            self.assertNotEqual(connection.connect_ex(("203.0.113.1", 443)), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
