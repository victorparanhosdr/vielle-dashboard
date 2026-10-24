"""Trusted entry point, mounted read-only. Validate bounds before importing tests."""
import importlib.util
import os
from pathlib import Path
import resource
import json
import sqlite3
import subprocess
import sys
import unittest


def bounded_integer(path, ceiling):
    value = Path(path).read_text().strip()
    if not value.isdigit() or not 0 < int(value) <= ceiling:
        raise RuntimeError("test container resource bound missing")


def main():
    allowed = {"PATH", "HOME", "TMPDIR", "BACKUP_TEST_ROOT", "BACKUP_TEST_AGE", "BACKUP_TEST_KEYGEN",
               "PYTHONDONTWRITEBYTECODE", "LANG"}
    if set(os.environ) - allowed or os.getuid() == 0 or sys.version_info[:2] != (3, 13):
        raise RuntimeError("unexpected test identity, environment, or Python version")
    bounded_integer("/sys/fs/cgroup/memory.max", 256 * 1024**2)
    bounded_integer("/sys/fs/cgroup/pids.max", 32)
    cpu, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
    if not cpu.isdigit() or not period.isdigit() or not 0 < int(cpu) <= int(period) // 2:
        raise RuntimeError("CPU quota missing")
    for name, ceiling in ((resource.RLIMIT_CPU, 30), (resource.RLIMIT_FSIZE, 1024**2),
                          (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_NPROC, 32), (resource.RLIMIT_CORE, 0)):
        soft, hard = resource.getrlimit(name)
        if soft < 0 or hard < 0 or soft > ceiling or hard > ceiling:
            raise RuntimeError("test process limit missing")
    disk = os.statvfs("/scratch")
    if disk.f_blocks * disk.f_frsize > 32 * 1024**2:
        raise RuntimeError("scratch disk limit missing")
    if len(Path("/proc/net/route").read_text().splitlines()) > 1:
        raise RuntimeError("external network route present")
    for directory in ("/scratch/home", "/scratch/temp"):
        Path(directory).mkdir(mode=0o700)
    age = subprocess.run([os.environ["BACKUP_TEST_AGE"], "--version"], check=True,
                         capture_output=True, text=True, timeout=5).stdout.strip()
    from cryptography import __version__ as cryptography_version
    print("TOOLCHAIN=" + json.dumps({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                                     "age": age, "cryptography": cryptography_version}), flush=True)
    sys.path.insert(0, "/src")
    suite = unittest.TestSuite()
    for name in ("offsite_backup", "offsite_backup_drive", "offsite_backup_google", "offsite_backup_service",
                 "offsite_backup_runtime", "offsite_backup_api"):
        spec = importlib.util.spec_from_file_location("test_" + name, "/src/tests/test_" + name + ".py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
