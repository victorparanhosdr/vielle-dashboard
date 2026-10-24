"""Offline-capable volume snapshots. Never imports the application or its workers."""
import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import time
from urllib.parse import quote

FORMAT = "doc4docs-volume-v1"
BLOCK = 1024 * 1024
EXCLUDED_ROOTS = {".offsite-backup", "lost+found"}
SQLITE_SUFFIXES = {".sqlite3", ".sqlite", ".db"}
SIDECARS = ("-wal", "-shm", "-journal")


class BackupError(Exception):
    """Only fixed codes may leave the backup process; never database exceptions."""


@dataclass(frozen=True)
class Limits:
    files: int = 30000
    bytes: int = 2 * 1024**3
    seconds: int = 1200
    databases: int = 128


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700)
    return path


def safe_relative(value):
    p = PurePosixPath(value)
    if (not value or p.is_absolute() or "\\" in value or "\x00" in value
            or any(x in {"", ".", ".."} for x in value.split("/"))):
        raise BackupError("unsafe_path")
    return p


def regular_open(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BackupError("unsupported_file")
        return os.fdopen(fd, "rb"), info
    except BaseException:
        os.close(fd)
        raise


def signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def inventory(root, limits):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise BackupError("invalid_source")
    result, total = {}, 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        parent = Path(folder)
        if parent == root:
            dirs[:] = [d for d in dirs if d not in EXCLUDED_ROOTS]
        for name in dirs:
            if (parent / name).is_symlink():
                raise BackupError("symlink")
        for name in files:
            path = parent / name
            if path.is_symlink():
                raise BackupError("symlink")
            info = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise BackupError("unsupported_file")
            relative = path.relative_to(root).as_posix()
            safe_relative(relative)
            result[relative] = info
            total += info.st_size
            if len(result) > limits.files or total > limits.bytes:
                raise BackupError("source_limit")
    if not result:
        raise BackupError("empty_source")
    return dict(sorted(result.items()))


def sqlite_readonly(path):
    connection = sqlite3.connect("file:" + quote(str(Path(path).absolute()), safe="/") + "?mode=ro", uri=True, timeout=1)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def sqlite_snapshot(source, destination, deadline):
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    def progress(*_):
        if time.monotonic() > deadline:
            raise BackupError("snapshot_timeout")
    with closing(sqlite_readonly(source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy, pages=128, progress=progress, sleep=0.05)
        copy.execute("PRAGMA journal_mode=DELETE")
        copy.execute("PRAGMA trusted_schema=OFF")
        copy.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        if copy.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise BackupError("sqlite_integrity")
        if copy.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupError("sqlite_foreign_keys")


def digest_file(path):
    handle, before = regular_open(path)
    with handle:
        sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
        count = 0
        for chunk in iter(lambda: handle.read(BLOCK), b""):
            count += len(chunk)
            if count > before.st_size:
                raise BackupError("source_changed")
            sha.update(chunk)
            md5.update(chunk)
        if count != before.st_size or signature(before) != signature(os.fstat(handle.fileno())):
            raise BackupError("source_changed")
    return {"sha256": sha.hexdigest(), "md5": md5.hexdigest(), "size": count}


class HashedReader:
    def __init__(self, source):
        self.source, self.hash = source, hashlib.sha256()
        self.count = 0

    def read(self, size=-1):
        value = self.source.read(size)
        self.count += len(value)
        self.hash.update(value)
        return value


def required_files(database, relative):
    """Check fixed active schemas, not the obsolete migration backup databases."""
    parent = PurePosixPath(relative).parent
    with closing(sqlite_readonly(database)) as c:
        c.row_factory = sqlite3.Row
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if len(PurePosixPath(relative).parts) == 3 and relative.startswith("crm/") and relative.endswith("/crm.sqlite3"):
            if "message_media" in tables:
                for row in c.execute("SELECT * FROM message_media WHERE download_state='ready'"):
                    key = row["followup_asset_id"] if "followup_asset_id" in row.keys() and row["followup_asset_id"] else row["id"]
                    yield str(parent / "media" / key), row["size"], row["sha256"]
            if "crm_followup_assets" in tables:
                for row in c.execute("SELECT id,size,sha256 FROM crm_followup_assets"):
                    yield str(parent / "media" / row["id"]), row["size"], row["sha256"]
            if "form_uploads" in tables:
                for row in c.execute("SELECT id,size FROM form_uploads"):
                    yield str(parent / "forms" / row["id"]), row["size"], ""
        if relative.startswith("tasks/") and relative.endswith("/tasks.sqlite3") and "task_files" in tables:
            for row in c.execute("SELECT id,size FROM task_files"):
                yield str(parent / "files" / row["id"]), row["size"], ""


def prepare(root, output, scratch, recipient, *, age="age", limits=Limits(), revision="", workspace=None):
    root, output, scratch = Path(root).absolute(), Path(output).absolute(), Path(scratch).absolute()
    if not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
        raise BackupError("invalid_recipient")
    if any(p.resolve() != p for p in (root, output, scratch)):
        raise BackupError("unsafe_source_path")
    allowed_workspace = Path(workspace).absolute() if workspace is not None else None
    if allowed_workspace is not None and (allowed_workspace != root / ".offsite-backup"
                                         or allowed_workspace.resolve() != allowed_workspace):
        raise BackupError("unsafe_destination")
    inside = lambda path: path.is_relative_to(root) and not (
        allowed_workspace is not None and path.is_relative_to(allowed_workspace))
    if output.exists() or scratch.exists() or inside(output) or inside(scratch) or output == scratch:
        raise BackupError("unsafe_destination")
    source = inventory(root, limits)
    if not any(Path(p).suffix in SQLITE_SUFFIXES for p in source):
        raise BackupError("missing_database")
    available = shutil.disk_usage(output.parent).free
    reserve = min(128 * BLOCK, max(BLOCK, limits.bytes // 16))
    if available < sum(i.st_size for i in source.values()) * 2 + reserve:
        raise BackupError("insufficient_space")
    deadline = time.monotonic() + limits.seconds
    private_directory(scratch)
    encrypted_fd, process, output_owned = None, None, False
    snapshots, manifest = {}, []
    started = datetime.now(timezone.utc).isoformat()
    try:
        for relative, before in source.items():
            path = root / relative
            if Path(relative).suffix not in SQLITE_SUFFIXES:
                continue
            if len(snapshots) >= limits.databases:
                raise BackupError("database_limit")
            with regular_open(path)[0] as handle:
                if handle.read(16) != b"SQLite format 3\x00":
                    raise BackupError("invalid_database")
            target = scratch / ("db-%03d.sqlite3" % len(snapshots))
            sqlite_snapshot(path, target, deadline)
            after = path.stat(follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise BackupError("source_replaced")
            snapshots[relative] = target
        encrypted_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        output_owned = True
        with os.fdopen(encrypted_fd, "wb") as destination:
            encrypted_fd = None
            process = subprocess.Popen([str(age), "--encrypt", "-r", recipient], stdin=subprocess.PIPE,
                stdout=destination, stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "GOMAXPROCS": "2", "GOMEMLIMIT": "64MiB"})
            total = 0
            with tarfile.open(fileobj=process.stdin, mode="w|gz", format=tarfile.PAX_FORMAT) as archive:
                for relative, before in source.items():
                    if any(relative.endswith(suffix) and relative[:-len(suffix)] in snapshots for suffix in SIDECARS):
                        continue
                    if time.monotonic() > deadline:
                        raise BackupError("snapshot_timeout")
                    path = snapshots.get(relative, root / relative)
                    handle, current = regular_open(path)
                    with handle:
                        if relative not in snapshots and signature(current) != signature(before):
                            raise BackupError("source_changed")
                        total += current.st_size
                        if total > limits.bytes:
                            raise BackupError("archive_limit")
                        item = tarfile.TarInfo("data/" + relative)
                        item.size, item.mode, item.mtime = current.st_size, 0o600, 0
                        reader = HashedReader(handle)
                        archive.addfile(item, reader)
                        if reader.count != current.st_size or signature(os.fstat(handle.fileno())) != signature(current):
                            raise BackupError("source_changed")
                        manifest.append({"path": relative, "size": item.size, "sha256": reader.hash.hexdigest(),
                                         "kind": "sqlite" if relative in snapshots else "file"})
                archived = {m["path"]: m for m in manifest}
                references = 0
                for relative, snapshot in snapshots.items():
                    for name, size, digest in required_files(snapshot, relative):
                        safe_relative(name)
                        actual = archived.get(name)
                        if not actual or actual["size"] != size or digest and actual["sha256"] != digest:
                            raise BackupError("missing_reference")
                        references += 1
                content = json.dumps({"format": FORMAT, "created_at": started, "revision": revision,
                    "consistency": "individual SQLite snapshots; stable files; not a global transaction",
                    "files": manifest, "checked_references": references}, separators=(",", ":")).encode()
                item = tarfile.TarInfo("manifest.json")
                item.size, item.mode, item.mtime = len(content), 0o600, 0
                archive.addfile(item, io.BytesIO(content))
            process.stdin.close()
            if process.wait(timeout=max(1, deadline - time.monotonic())) != 0:
                raise BackupError("encryption_failed")
            destination.flush()
            os.fsync(destination.fileno())
        return {"format": FORMAT, "created_at": started, "files": len(manifest), "databases": len(snapshots),
                "checked_references": references, **digest_file(output)}
    except BaseException:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if encrypted_fd is not None:
            os.close(encrypted_fd)
        if output_owned:
            output.unlink(missing_ok=True)
        raise
    finally:
        if process and process.stdin and not process.stdin.closed:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        shutil.rmtree(scratch)


def check_restore(encrypted, identity, destination, *, age="age", limits=Limits()):
    destination = Path(destination).absolute()
    private_directory(destination)
    process = None
    files, seen, total, manifest = {}, set(), 0, None
    deadline = time.monotonic() + limits.seconds
    try:
        process = subprocess.Popen([str(age), "--decrypt", "-i", str(identity), str(encrypted)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "GOMAXPROCS": "2", "GOMEMLIMIT": "64MiB"})
        with tarfile.open(fileobj=process.stdout, mode="r|gz") as archive:
            for member in archive:
                if not member.isfile():
                    raise BackupError("unsupported_archive_member")
                relative = safe_relative(member.name)
                if member.name in seen or not (member.name == "manifest.json" or member.name.startswith("data/")):
                    raise BackupError("unsafe_archive")
                seen.add(member.name)
                total += member.size
                if member.size < 0 or total > limits.bytes + 32 * BLOCK or time.monotonic() > deadline:
                    raise BackupError("restore_limit")
                data = archive.extractfile(member)
                if member.name == "manifest.json":
                    if member.size > 32 * BLOCK or manifest is not None:
                        raise BackupError("invalid_manifest")
                    manifest = json.loads(data.read())
                    continue
                if len(files) >= limits.files:
                    raise BackupError("restore_limit")
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                sha, count = hashlib.sha256(), 0
                with os.fdopen(fd, "wb") as result:
                    for chunk in iter(lambda: data.read(BLOCK), b""):
                        count += len(chunk)
                        if count > member.size or time.monotonic() > deadline:
                            raise BackupError("restore_limit")
                        result.write(chunk)
                        sha.update(chunk)
                if count != member.size:
                    raise BackupError("truncated_archive")
                files[str(PurePosixPath(member.name).relative_to("data"))] = {"size": count, "sha256": sha.hexdigest()}
        # Consume the authenticated stream to its end, not just TAR's end marker.
        for chunk in iter(lambda: process.stdout.read(BLOCK), b""):
            if time.monotonic() > deadline:
                raise BackupError("restore_timeout")
        if process.wait(timeout=max(1, deadline - time.monotonic())) != 0:
            raise BackupError("decryption_failed")
        if not manifest or manifest.get("format") != FORMAT or not isinstance(manifest.get("files"), list):
            raise BackupError("invalid_manifest")
        expected = {}
        databases = 0
        for item in manifest["files"]:
            if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                    or type(item.get("size")) is not int or item["size"] < 0
                    or not isinstance(item.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                    or item.get("kind") not in {"sqlite", "file"}):
                raise BackupError("invalid_manifest")
            path = str(safe_relative(item["path"]))
            if path in expected or path not in files:
                raise BackupError("invalid_manifest")
            expected[path] = {"size": item["size"], "sha256": item["sha256"]}
            if item["kind"] == "sqlite":
                databases += 1
                if databases > limits.databases:
                    raise BackupError("restore_limit")
                with closing(sqlite_readonly(destination / "data" / path)) as c:
                    c.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                    if c.execute("PRAGMA integrity_check").fetchone() != ("ok",) or c.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        raise BackupError("sqlite_integrity")
        if expected != files or not databases:
            raise BackupError("manifest_mismatch")
        return {"ok": True, "files": len(files), "databases": databases, "bytes": total}
    except BaseException:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        shutil.rmtree(destination)
        raise
    finally:
        if process and process.stdout:
            process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check-restore"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--scratch")
    parser.add_argument("--recipient")
    parser.add_argument("--identity")
    parser.add_argument("--age", default="age")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            if not args.recipient or not args.scratch:
                raise BackupError("missing_configuration")
            result = prepare(args.source, args.output, args.scratch, args.recipient, age=args.age)
        else:
            if not args.identity:
                raise BackupError("missing_configuration")
            result = check_restore(args.source, args.identity, args.output, age=args.age)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"ok": False, "code": str(exc) if isinstance(exc, BackupError) else "backup_failed"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
