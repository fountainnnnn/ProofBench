"""Railway process entrypoint: prepare the mounted volume, then drop root."""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_UID = 10001
APP_GID = 10001


def _port() -> str:
    value = os.environ.get("PORT", "8000").strip()
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise RuntimeError("PORT must be an integer between 1 and 65535")
    return value


def main() -> None:
    runtime_root = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/app/runtime")).resolve()
    if not runtime_root.is_absolute() or runtime_root == Path("/"):
        raise RuntimeError("Railway runtime volume must use a dedicated absolute mount path")

    runs_root = runtime_root / "runs"
    uploads_root = runtime_root / "data" / "uploads"
    for directory in (runtime_root, runs_root, uploads_root):
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, APP_UID, APP_GID)

    environment = dict(os.environ)
    environment.setdefault("PROOFBENCH_RUNS_ROOT", str(runs_root))
    environment.setdefault("PROOFBENCH_DATASET_ROOT", str(uploads_root))
    environment.setdefault("PROOFBENCH_SANDBOX_LEDGER", str(runtime_root / "sandbox_ledger.sqlite3"))
    environment.setdefault("PROOFBENCH_WEB_ROOT", "/app/web-dist")

    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(APP_GID)
        os.setuid(APP_UID)

    command = [
        sys.executable, "-m", "uvicorn", "server.main:app",
        "--host", "0.0.0.0", "--port", _port(), "--workers", "1",
        "--proxy-headers", "--forwarded-allow-ips=*",
        "--timeout-graceful-shutdown", "40",
    ]
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
