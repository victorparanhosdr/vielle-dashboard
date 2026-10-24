"""Disabled-by-default backup job with resumable encrypted uploads."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import uuid

from offsite_backup import BackupError, Limits, prepare, regular_open
from offsite_backup_drive import Drive
from offsite_backup_google import AccountToken


def secure_directory(path):
    path = Path(path)
    if path.resolve() != path.absolute():
        raise BackupError("unsafe_backup_workspace")
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BackupError("unsafe_backup_workspace")
    return path


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def exclusive_job(workspace):
    fd = os.open(workspace / "job.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BackupError("unsafe_backup_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("backup_already_running") from None
        yield
    finally:
        os.close(fd)


def read_state(path):
    if not path.exists():
        return {"version": 1, "folder_id": "", "active": None, "last_uploaded": 0}
    with regular_open(path)[0] as handle:
        info = os.fstat(handle.fileno())
        if info.st_mode & 0o077 or info.st_uid != os.getuid() or info.st_size > 65536:
            raise BackupError("unsafe_backup_journal")
        value = json.load(handle)
    if (not isinstance(value, dict) or value.get("version") != 1
            or type(value.get("last_uploaded")) not in {float, int}
            or not math.isfinite(value["last_uploaded"]) or value["last_uploaded"] < 0):
        raise BackupError("invalid_backup_journal")
    return value


def run_once(environment=None, *, drive_factory=Drive, token_factory=AccountToken, snapshot=prepare):
    env = os.environ if environment is None else environment
    if env.get("DOC4DOCS_OFFSITE_BACKUP_ENABLED") != "1":
        return {"status": "disabled"}
    recipient = env.get("DOC4DOCS_OFFSITE_RECIPIENT", "")
    owner = env.get("DOC4DOCS_OFFSITE_GOOGLE_OWNER", "").casefold()
    account = env.get("DOC4DOCS_OFFSITE_GOOGLE_ACCOUNT_ID", "")
    source = Path(env.get("DATA_DIR", "")).absolute()
    if (not env.get("DATA_DIR") or source.resolve() != source or not source.is_dir()
            or not re.fullmatch(r"age1[0-9a-z]{58}", recipient)):
        raise BackupError("missing_backup_configuration")
    budget = int(env.get("DOC4DOCS_OFFSITE_MAX_BYTES", str(2 * 1024**3)))
    if not 64 * 1024**2 <= budget <= 4 * 1024**3:
        raise BackupError("invalid_backup_limit")
    binding = hashlib.sha256(json.dumps([str(source), recipient, owner, account]).encode()).hexdigest()
    workspace = secure_directory(source / ".offsite-backup")
    state_path = workspace / "state.json"
    with exclusive_job(workspace):
        state = read_state(state_path)
        if state.get("binding") not in {None, binding}:
            raise BackupError("backup_configuration_changed")
        state["binding"] = binding
        # Allow the next daily slot despite snapshot/upload duration in the previous run.
        if state["active"] is None and time.time() - state["last_uploaded"] < 23 * 3600:
            return {"status": "not_due"}
        token = token_factory(source / "crm/vielle/crm.sqlite3", source / "crm/vielle/forms/oauth.key",
            account, owner, env.get("CRM_GOOGLE_CLIENT_ID", ""), env.get("CRM_GOOGLE_CLIENT_SECRET", ""))
        drive = drive_factory(token, owner)
        drive.identity()
        if not state["folder_id"]:
            state["folder_id"] = drive.new_id()
            atomic_json(state_path, state)
        drive.folder(state["folder_id"])
        if state["active"] is None:
            state["active"] = {"job_id": uuid.uuid4().hex, "file_id": drive.new_id(), "phase": "preparing",
                               "attempts": 0, "session": "", "offset": 0, "verified": False}
            atomic_json(state_path, state)
        job = state["active"]
        if (not isinstance(job, dict) or not re.fullmatch(r"[a-f0-9]{32}", job.get("job_id", ""))
                or job.get("phase") not in {"preparing", "prepared"} or type(job.get("attempts")) is not int
                or not 0 <= job["attempts"] < 6):
            raise BackupError("backup_job_needs_review")
        job["attempts"] += 1
        atomic_json(state_path, state)
        jobs = secure_directory(workspace / "jobs")
        jobroot = secure_directory(jobs / job["job_id"])
        packet, scratch = jobroot / "packet.age", jobroot / "stage"
        try:
            if job["phase"] == "preparing":
                # These are only this journal's unuploaded, interrupted artifacts.
                if packet.exists():
                    with regular_open(packet)[0]:
                        pass
                    packet.unlink()
                if scratch.exists():
                    if scratch.is_symlink() or not scratch.is_dir():
                        raise BackupError("unsafe_backup_workspace")
                    shutil.rmtree(scratch)
                job["export"] = snapshot(source, packet, scratch, recipient, workspace=workspace,
                    age=env.get("DOC4DOCS_OFFSITE_AGE_BIN", "age"), limits=Limits(bytes=budget),
                    revision=env.get("RAILWAY_GIT_COMMIT_SHA", ""))
                job["phase"] = "prepared"
                atomic_json(state_path, state)
            def checkpoint(value):
                job.update(value)
                atomic_json(state_path, state)
            result = drive.upload(packet, state["folder_id"], job["file_id"], job["export"], checkpoint,
                                  session=job.get("session", ""))
            receipts = secure_directory(workspace / "receipts")
            completed = time.time()
            atomic_json(receipts / (job["job_id"] + ".json"), {"version": 1, "status": "uploaded_checksum_verified",
                "uploaded_at": completed, "file": result, "export": job["export"]})
            state.update(last_uploaded=completed, active=None, last_error="")
            atomic_json(state_path, state)
            shutil.rmtree(jobroot)
            return {"status": "uploaded_checksum_verified", "size": result["size"]}
        except Exception as exc:
            state["last_error"] = str(exc) if isinstance(exc, BackupError) else "backup_failed"
            atomic_json(state_path, state)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run-once",))
    parser.parse_args()
    try:
        print(json.dumps(run_once()))
    except Exception as exc:
        print(json.dumps({"status": "failed", "code": str(exc) if isinstance(exc, BackupError) else "backup_failed"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
