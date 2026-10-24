"""Pure synthetic Google responses; no sockets or production identities."""
from collections import deque
import json
import os
from pathlib import Path
import shutil
import unittest

from offsite_backup import BackupError, digest_file
from offsite_backup_drive import Drive, FOLDER_MIME, MARKER, Reply, USERINFO, allowed_url

OWNER = "owner@example.invalid"
FOLDER = "d" * 20
FILE = "f" * 20
SESSION = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=fixture"


def reply(data, status=200, headers=None):
    return Reply(status, headers or {}, json.dumps(data).encode())


def metadata(ident, *, folder=False, expected=None):
    value = {"id": ident, "mimeType": FOLDER_MIME if folder else "application/octet-stream",
        "trashed": False, "shared": False, "owners": [{"emailAddress": OWNER, "me": True}],
        "permissions": [{"type": "user", "role": "owner", "emailAddress": OWNER}], "appProperties": dict(MARKER)}
    if expected:
        value.update(parents=[FOLDER], size=str(expected["size"]), md5Checksum=expected["md5"], sha256Checksum=expected["sha256"])
        value["appProperties"]["cipher_sha256"] = expected["sha256"]
    return value


class Scripted:
    def __init__(self, replies, hook=None):
        self.replies, self.calls, self.hook = deque(replies), [], hook

    def __call__(self, url, method, data, headers):
        self.calls.append((url, method, data, headers))
        if self.hook:
            self.hook(url, method, data, headers)
        result = self.replies.popleft()
        if isinstance(result, Exception):
            raise result
        return result


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(os.environ["BACKUP_TEST_ROOT"]) / "drive-case"
        self.work.mkdir()
        self.packet = self.work / "packet.age"
        self.packet.write_bytes(b"age-encryption.org/v1\nsynthetic ciphertext only\n")
        self.expected = digest_file(self.packet)
        self.checkpoints = []
        self.profile = reply({"sub": "fixture-owner", "email": OWNER, "email_verified": True})
        self.folder = reply(metadata(FOLDER, folder=True))
        self.file = reply(metadata(FILE, expected=self.expected))
        self.missing = Reply(404, {})
        self.begin = Reply(200, {"location": SESSION})

    def tearDown(self):
        shutil.rmtree(self.work)

    def client(self, sequence, hook=None):
        self.transport = Scripted(sequence, hook)
        return Drive(lambda: "fixture-token", OWNER, transport=self.transport)

    def send(self, drive, **kwargs):
        return drive.upload(self.packet, FOLDER, FILE, self.expected, self.checkpoints.append, **kwargs)

    def test_upload_checkpoints_session_before_bytes_and_checks_checksums(self):
        def hook(url, method, data, headers):
            if method == "PUT" and data:
                self.assertEqual(self.checkpoints[-1]["session"], SESSION)
        drive = self.client([self.profile, self.folder, self.missing, self.begin, reply({"id": FILE}), self.folder, self.file], hook)
        self.assertTrue(self.send(drive)["verified"])
        self.assertTrue(self.checkpoints[-1]["verified"])
        self.assertEqual(self.checkpoints[-1]["session"], "")
        self.assertFalse(self.transport.replies)

    def test_shared_destination_rejected_before_upload(self):
        shared = metadata(FOLDER, folder=True)
        shared["shared"] = True
        drive = self.client([self.profile, reply(shared)])
        with self.assertRaisesRegex(BackupError, "not_private"):
            self.send(drive)
        self.assertFalse(any(call[1] == "POST" for call in self.transport.calls))

    def test_wrong_google_account_rejected(self):
        drive = self.client([reply({"sub": "other", "email": "other@example.invalid", "email_verified": True})])
        with self.assertRaisesRegex(BackupError, "owner_mismatch"):
            self.send(drive)
        self.assertEqual(len(self.transport.calls), 1)

    def test_resume_queries_provider_offset_instead_of_trusting_local_offset(self):
        drive = self.client([self.profile, self.folder, self.missing,
            Reply(308, {"range": "bytes=0-4"}), reply({"id": FILE}), self.folder, self.file])
        self.send(drive, session=SESSION)
        put = [c for c in self.transport.calls if c[1] == "PUT"]
        self.assertEqual(put[0][2], b"")
        self.assertEqual(put[1][2], self.packet.read_bytes()[5:])
        self.assertTrue(put[1][3]["Content-Range"].startswith("bytes 5-"))
        self.assertFalse(any(c[1] == "POST" for c in self.transport.calls))

    def test_expired_session_reuses_same_file_id(self):
        drive = self.client([self.profile, self.folder, self.missing, self.missing,
                             self.begin, reply({"id": FILE}), self.folder, self.file])
        self.send(drive, session=SESSION)
        create = [c for c in self.transport.calls if c[1] == "POST"]
        self.assertEqual(len(create), 1)
        self.assertEqual(json.loads(create[0][2])["id"], FILE)

    def test_uncertain_write_does_not_retry_blindly(self):
        drive = self.client([self.profile, self.folder, self.missing, self.begin, BackupError("google_response_uncertain")])
        with self.assertRaisesRegex(BackupError, "uncertain"):
            self.send(drive)
        self.assertEqual(len([c for c in self.transport.calls if c[1] == "PUT"]), 1)
        self.assertEqual(self.checkpoints[-1]["session"], SESSION)

    def test_completed_indeterminate_upload_is_not_created_again(self):
        drive = self.client([self.profile, self.folder, self.file])
        self.assertTrue(self.send(drive, session=SESSION)["verified"])
        self.assertFalse(any(c[1] in {"POST", "PUT"} for c in self.transport.calls))

    def test_untrusted_session_cannot_receive_authorization(self):
        drive = self.client([self.profile, self.folder, self.missing])
        with self.assertRaises(BackupError):
            self.send(drive, session="https://example.invalid/upload?upload_id=fixture")
        self.assertTrue(all("example.invalid" not in call[0] for call in self.transport.calls))
        for url in ("http://www.googleapis.com/drive/v3/files", SESSION.replace("googleapis.com", "googleapis.com.evil.invalid"),
                    SESSION.replace("https://", "https://user:password@"), SESSION + "#fragment"):
            with self.assertRaises(BackupError):
                allowed_url(url, session=True)

    def test_bad_provider_checksum_not_reported_as_success(self):
        bad = metadata(FILE, expected=self.expected)
        bad["md5Checksum"] = "0" * 32
        drive = self.client([self.profile, self.folder, self.missing, self.begin,
                             reply({"id": FILE}), self.folder, reply(bad)])
        with self.assertRaisesRegex(BackupError, "file_mismatch"):
            self.send(drive)
        self.assertFalse(self.checkpoints[-1]["verified"])

    def test_plaintext_and_modified_packet_are_rejected(self):
        drive = self.client([])
        self.packet.write_bytes(b"synthetic plaintext, never send this file")
        with self.assertRaisesRegex(BackupError, "not_an_age_ciphertext"):
            self.send(drive)
        self.assertFalse(self.transport.calls)
        self.packet.write_bytes(b"age-encryption.org/v1\nchanged synthetic ciphertext\n")
        drive = self.client([self.profile, self.folder])
        with self.assertRaisesRegex(BackupError, "ciphertext_changed"):
            self.send(drive)
        self.assertFalse(any(call[1] in {"POST", "PUT"} for call in self.transport.calls))

    def test_folder_uses_preallocated_id_and_never_shares_it(self):
        drive = self.client([self.profile, self.missing, reply({"id": FOLDER}), self.folder])
        drive.folder(FOLDER)
        create = [c for c in self.transport.calls if c[1] == "POST"]
        self.assertEqual(json.loads(create[0][2])["id"], FOLDER)
        self.assertFalse(any("permissions" in c[0].split("?")[0] for c in self.transport.calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
