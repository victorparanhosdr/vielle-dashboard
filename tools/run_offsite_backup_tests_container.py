"""Trusted host runner. Never installs tools, pulls images, or mounts live data."""
import argparse
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid

MODULES = ("offsite_backup", "offsite_backup_drive", "offsite_backup_google", "offsite_backup_service",
           "offsite_backup_runtime", "offsite_backup_api")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Already provisioned local image ID: sha256:...")
    parser.add_argument("--socket", required=True, help="Local Docker Unix socket only")
    parser.add_argument("--docker", default=shutil.which("docker"))
    args = parser.parse_args()
    if not args.docker or not re.fullmatch(r"sha256:[a-f0-9]{64}", args.image):
        raise SystemExit("A trusted local Docker executable and pre-provisioned image ID are required.")
    socket = Path(args.socket).resolve()
    if not stat.S_ISSOCK(socket.stat().st_mode):
        raise SystemExit("A local Docker Unix socket is required.")
    target = Path(__file__).resolve().parent.parent
    source_files = [target / (name + ".py") for name in MODULES]
    source_files += [target / "tests" / ("test_" + name + ".py") for name in MODULES]
    source_files += [target / "tools/offsite_backup_test_entry.py"]
    if any(not p.is_file() or p.is_symlink() for p in source_files):
        raise SystemExit("Missing or symlinked allowlisted test source.")
    prefix = [str(Path(args.docker).resolve()), "--host", "unix://" + str(socket)]
    name = "doc4docs-backup-test-" + uuid.uuid4().hex
    process = None
    with tempfile.TemporaryDirectory(prefix="doc4docs-backup-docker-") as home:
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "DOCKER_CONFIG": home}
        inspected = subprocess.run(prefix + ["image", "inspect", "--format", "{{.Id}}", args.image],
                                   env=env, capture_output=True, text=True, timeout=10)
        if inspected.returncode or inspected.stdout.strip() != args.image:
            raise SystemExit("Required image is not already available locally; nothing was downloaded.")
        command = prefix + ["run", "--rm", "--pull", "never", "--name", name, "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
            "--ipc", "none", "--cpus", "0.5", "--memory", "256m", "--memory-swap", "256m", "--pids-limit", "32",
            "--ulimit", "cpu=30:30", "--ulimit", "nproc=32:32", "--ulimit", "fsize=1048576:1048576",
            "--ulimit", "nofile=64:64", "--ulimit", "core=0:0", "--workdir", "/scratch",
            "--tmpfs", "/scratch:rw,nosuid,nodev,noexec,size=32m,nr_inodes=8192,mode=0700,uid=65534,gid=65534"]
        for path in source_files:
            destination = "/runner.py" if path.name == "offsite_backup_test_entry.py" else "/src/" + path.relative_to(target).as_posix()
            command += ["--mount", f"type=bind,source={path},target={destination},readonly"]
        command += ["--entrypoint", "/usr/bin/env", args.image, "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
            "HOME=/scratch/home", "TMPDIR=/scratch/temp", "LANG=C.UTF-8", "PYTHONDONTWRITEBYTECODE=1",
            "BACKUP_TEST_ROOT=/scratch", "BACKUP_TEST_AGE=/usr/bin/age", "BACKUP_TEST_KEYGEN=/usr/bin/age-keygen",
            "/usr/local/bin/python3", "-I", "-B", "/runner.py"]
        selector = selectors.DefaultSelector()
        try:
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            output, deadline = bytearray(), time.monotonic() + 120
            while process.poll() is None:
                for key, _ in selector.select(timeout=0.05):
                    output.extend(os.read(key.fd, 8192))
                if len(output) > 65536 or time.monotonic() > deadline:
                    raise TimeoutError("Container test output or wall-clock bound exceeded.")
            output.extend(process.stdout.read(65537 - min(len(output), 65536)) or b"")
            if len(output) > 65536:
                raise RuntimeError("Container test output limit exceeded.")
            print(output.decode("utf-8", "replace"))
            return process.returncode
        finally:
            # Killing the client alone is not enough: remove the container too.
            subprocess.run(prefix + ["rm", "--force", name], env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15)
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            selector.close()


if __name__ == "__main__":
    raise SystemExit(main())
