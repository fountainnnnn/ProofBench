"""Daytona sandbox lifecycle manager (CONTRACTS section 7).

Sandboxes may be pre-warmed, but are never reused after a candidate has run in
them.  Destruction on release is intentional: a remote workspace cannot be
proven clean enough to cross candidate or tenant boundaries.
"""

from __future__ import annotations

import os
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


_LEDGER_LOCK = threading.RLock()
_PROCESS_WORKER_ID = os.environ.get(
    "PROOFBENCH_WORKER_ID", f"pid-{os.getpid()}-{uuid.uuid4().hex[:8]}"
)


class _SandboxLedger:
    """Small durable ownership ledger for crash-time orphan recovery."""

    def __init__(self, path: str, deployment: str):
        self.path = str(Path(path).resolve())
        self.deployment = deployment
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with _LEDGER_LOCK, sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sandboxes ("
                "deployment TEXT NOT NULL, owner_key TEXT NOT NULL, "
                "sandbox_id TEXT NOT NULL, created_at REAL NOT NULL, "
                "worker_id TEXT NOT NULL, lease_expires_at REAL NOT NULL, "
                "PRIMARY KEY (deployment, sandbox_id))"
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(sandboxes)")
            }
            if "worker_id" not in columns:
                connection.execute(
                    "ALTER TABLE sandboxes ADD COLUMN worker_id TEXT NOT NULL DEFAULT 'legacy'"
                )
            if "lease_expires_at" not in columns:
                connection.execute(
                    "ALTER TABLE sandboxes ADD COLUMN lease_expires_at REAL NOT NULL DEFAULT 0"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sandbox_reconcilers ("
                "deployment TEXT PRIMARY KEY, worker_id TEXT NOT NULL, "
                "lease_expires_at REAL NOT NULL)"
            )

    def add(
        self,
        owner_key: str,
        sandbox_id: str,
        worker_id: str,
        created_at: float,
        lease_expires_at: float,
    ) -> None:
        with _LEDGER_LOCK, sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO sandboxes(deployment, owner_key, sandbox_id, created_at, "
                "worker_id, lease_expires_at) VALUES(?,?,?,?,?,?)",
                (
                    self.deployment,
                    owner_key,
                    sandbox_id,
                    created_at,
                    worker_id,
                    lease_expires_at,
                ),
            )

    def renew(self, worker_id: str, sandbox_ids: list[str], lease_expires_at: float) -> None:
        if not sandbox_ids:
            return
        placeholders = ",".join("?" for _ in sandbox_ids)
        with _LEDGER_LOCK, sqlite3.connect(self.path) as connection:
            connection.execute(
                f"UPDATE sandboxes SET lease_expires_at=? WHERE deployment=? "
                f"AND worker_id=? AND sandbox_id IN ({placeholders})",
                (lease_expires_at, self.deployment, worker_id, *sandbox_ids),
            )

    def remove(self, sandbox_id: str) -> None:
        with _LEDGER_LOCK, sqlite3.connect(self.path) as connection:
            connection.execute(
                "DELETE FROM sandboxes WHERE deployment=? AND sandbox_id=?",
                (self.deployment, sandbox_id),
            )

    def entries(self) -> list[dict[str, object]]:
        with _LEDGER_LOCK, sqlite3.connect(self.path) as connection:
            return [
                {
                    "owner_key": str(owner_key),
                    "sandbox_id": str(sandbox_id),
                    "created_at": _ledger_timestamp(created_at),
                    "worker_id": str(worker_id),
                    "lease_expires_at": float(lease_expires_at),
                }
                for owner_key, sandbox_id, created_at, worker_id, lease_expires_at in connection.execute(
                    "SELECT owner_key, sandbox_id, created_at, worker_id, lease_expires_at "
                    "FROM sandboxes "
                    "WHERE deployment=? ORDER BY created_at, sandbox_id",
                    (self.deployment,),
                )
            ]

    def try_acquire_reconciler(
        self, worker_id: str, now: float, lease_expires_at: float
    ) -> bool:
        """Atomically acquire the deployment-scoped startup reconciler lease."""
        with _LEDGER_LOCK, sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT worker_id, lease_expires_at FROM sandbox_reconcilers "
                "WHERE deployment=?",
                (self.deployment,),
            ).fetchone()
            if row and str(row[0]) != worker_id and float(row[1]) > now:
                return False
            connection.execute(
                "INSERT OR REPLACE INTO sandbox_reconcilers"
                "(deployment, worker_id, lease_expires_at) VALUES(?,?,?)",
                (self.deployment, worker_id, lease_expires_at),
            )
            return True


def _ledger_timestamp(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()


def _text(resp) -> str:
    """Best-effort stdout text out of a Daytona ExecuteResponse."""
    for attr in ("result", "stdout", "output"):
        val = getattr(resp, attr, None)
        if isinstance(val, str):
            return val
    return resp if isinstance(resp, str) else str(resp)


@dataclass
class SandboxHandle:
    id: str
    label: str
    sandbox: object = field(repr=False)  # daytona Sandbox


class SandboxPool:
    """Own all Daytona resources created for one orchestrator instance."""

    def __init__(
        self,
        size: int = 4,
        owner_key: str = "unscoped",
        ledger_path: str | None = None,
        deployment: str | None = None,
        worker_id: str | None = None,
        lease_seconds: float = 1800,
        orphan_min_age_seconds: float = 300,
        clock=None,
    ):
        self.size = size
        self.owner_key = str(owner_key)
        self.worker_id = str(worker_id or _PROCESS_WORKER_ID)
        self.lease_seconds = max(60.0, float(lease_seconds))
        self.orphan_min_age_seconds = max(0.0, float(orphan_min_age_seconds))
        self._clock = clock or time.time
        self._daytona = None
        self._available: queue.Queue = queue.Queue()
        self._tracked: dict[int, object] = {}
        self._leased: dict[str, object] = {}
        self._remote_ids: dict[int, str] = {}
        self._lock = threading.RLock()
        self._started = False
        self._lease_stop = threading.Event()
        self._lease_thread: threading.Thread | None = None
        # Pin the interpreter family used by candidate integrations. Daytona's
        # moving default image can advance beyond binary wheels published by
        # OCR runtimes (notably PaddlePaddle), turning a valid adapter into an
        # environment-dependent failure.
        self.image = os.environ.get("DAYTONA_SANDBOX_IMAGE", "python:3.12-slim").strip()
        default_ledger = (
            Path(__file__).resolve().parent.parent / "runs" / "sandbox_ledger.sqlite3"
        )
        self._ledger = _SandboxLedger(
            ledger_path
            or os.environ.get("PROOFBENCH_SANDBOX_LEDGER", str(default_ledger)),
            deployment
            or os.environ.get("PROOFBENCH_DEPLOYMENT_ID", "local"),
        )

    def _client(self):
        if self._daytona is None:
            from daytona import Daytona

            self._daytona = Daytona()  # configured from DAYTONA_* env vars
        return self._daytona

    def _create_one(self):
        try:
            from daytona import CreateSandboxFromImageParams

            create_params = CreateSandboxFromImageParams(
                image=self.image, language="python"
            )
        except ModuleNotFoundError as exc:
            # Unit tests and compatible injected clients do not need the
            # Daytona SDK merely to exercise lifecycle/ledger behaviour.  A
            # real, lazily-created Daytona client still fails with an explicit
            # dependency error in _client().
            if self._daytona is None:
                raise RuntimeError(
                    "Daytona SDK is required to create remote sandboxes"
                ) from exc
            create_params = {"image": self.image, "language": "python"}

        sandbox = self._client().create(
            create_params,
            timeout=300,
        )
        remote_id = str(getattr(sandbox, "id", "") or "")
        if not remote_id:
            try:
                self._client().delete(sandbox)
            finally:
                raise RuntimeError("created sandbox did not expose a remote id")
        try:
            now = self._clock()
            self._ledger.add(
                self.owner_key,
                remote_id,
                self.worker_id,
                now,
                now + self.lease_seconds,
            )
        except Exception:
            self._client().delete(sandbox)
            raise
        with self._lock:
            self._tracked[id(sandbox)] = sandbox
            self._remote_ids[id(sandbox)] = remote_id
        return sandbox

    def _delete(self, sandbox: object) -> Exception | None:
        try:
            if self._daytona is not None and hasattr(self._daytona, "delete"):
                self._daytona.delete(sandbox)
            else:
                sandbox.delete()
            remote_id = self._remote_ids.get(id(sandbox))
            if remote_id:
                self._ledger.remove(remote_id)
        except Exception as exc:
            return exc
        return None

    def renew_lease(self) -> None:
        """Extend this worker's durable leases for all active sandboxes."""
        with self._lock:
            sandbox_ids = list(self._remote_ids.values())
        self._ledger.renew(
            self.worker_id, sandbox_ids, self._clock() + self.lease_seconds
        )

    def _start_lease_heartbeat(self) -> None:
        with self._lock:
            if self._lease_thread is not None and self._lease_thread.is_alive():
                return
            self._lease_stop.clear()

            def heartbeat():
                interval = max(20.0, self.lease_seconds / 3.0)
                while not self._lease_stop.wait(interval):
                    try:
                        self.renew_lease()
                    except Exception:
                        # A later operation renews synchronously and surfaces
                        # provider/ledger failures at the actual use boundary.
                        pass

            self._lease_thread = threading.Thread(target=heartbeat, daemon=True)
            self._lease_thread.start()

    def reconcile_orphans(
        self,
        *,
        startup_leader: bool = False,
        now: float | None = None,
        minimum_age_seconds: float | None = None,
    ) -> dict:
        """Delete only ledger-owned sandboxes for this deployment.

        Returns a report suitable for startup logging. Failed lookups/deletes
        remain ledgered for a later retry and are never silently discarded.
        """
        deleted: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        if not startup_leader:
            return {
                "deleted": deleted,
                "failures": failures,
                "skipped": [{"reason": "startup leader coordination required"}],
            }
        current_time = self._clock() if now is None else float(now)
        if not self._ledger.try_acquire_reconciler(
            self.worker_id, current_time, current_time + 60.0
        ):
            return {
                "deleted": deleted,
                "failures": failures,
                "skipped": [{"reason": "startup reconciler lease held"}],
            }
        minimum_age = (
            self.orphan_min_age_seconds
            if minimum_age_seconds is None
            else max(0.0, float(minimum_age_seconds))
        )
        client = self._client()
        for entry in self._ledger.entries():
            owner_key = str(entry["owner_key"])
            sandbox_id = str(entry["sandbox_id"])
            if float(entry["lease_expires_at"]) > current_time:
                skipped.append(
                    {"owner_key": owner_key, "sandbox_id": sandbox_id, "reason": "active lease"}
                )
                continue
            if current_time - float(entry["created_at"]) < minimum_age:
                skipped.append(
                    {"owner_key": owner_key, "sandbox_id": sandbox_id, "reason": "minimum age"}
                )
                continue
            try:
                sandbox = client.get(sandbox_id)
                client.delete(sandbox)
                self._ledger.remove(sandbox_id)
                deleted.append({"owner_key": owner_key, "sandbox_id": sandbox_id})
            except Exception as exc:
                failures.append(
                    {
                        "owner_key": owner_key,
                        "sandbox_id": sandbox_id,
                        "error": type(exc).__name__,
                    }
                )
        return {"deleted": deleted, "failures": failures, "skipped": skipped}

    def start(self) -> None:
        """Pre-warm ``size`` clean sandboxes in parallel."""
        with self._lock:
            if self._started:
                return
            self._started = True

        def warm():
            try:
                self._available.put(self._create_one())
            except Exception:
                # acquire() creates on demand and will surface provider errors.
                pass

        threads = [threading.Thread(target=warm, daemon=True) for _ in range(self.size)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self._start_lease_heartbeat()

    def acquire(self, label: str) -> SandboxHandle:
        """Lease a clean sandbox and return an opaque, one-use handle."""
        try:
            sandbox = self._available.get_nowait()
        except queue.Empty:
            sandbox = self._create_one()
        handle = SandboxHandle(
            id=f"{label}-{uuid.uuid4().hex[:8]}", label=label, sandbox=sandbox
        )
        with self._lock:
            self._tracked[id(sandbox)] = sandbox
            self._leased[handle.id] = sandbox
        return handle

    def _sandbox_for(self, handle: SandboxHandle) -> object:
        with self._lock:
            sandbox = self._leased.get(handle.id)
        if sandbox is not handle.sandbox:
            # Test/local backends may override acquire() and own their handle
            # lifecycle. Base-pool handles remain strictly one-use.
            if type(self).acquire is not SandboxPool.acquire:
                return handle.sandbox
            raise ValueError("sandbox handle is no longer active")
        remote_id = self._remote_ids.get(id(sandbox))
        if remote_id:
            self._ledger.renew(
                self.worker_id,
                [remote_id],
                self._clock() + self.lease_seconds,
            )
        return sandbox

    def exec(self, h: SandboxHandle, cmd: str, timeout: int = 120) -> str:
        sandbox = self._sandbox_for(h)
        response = sandbox.process.exec(cmd, timeout=timeout)
        exit_code = getattr(response, "exit_code", getattr(response, "code", None))
        if exit_code not in (None, 0, "0"):
            raise RuntimeError(f"sandbox command failed with exit code {exit_code}: {_text(response)[-1200:]}")
        return _text(response)

    def run_python(self, h: SandboxHandle, code: str, timeout: int = 180) -> str:
        sandbox = self._sandbox_for(h)
        return _text(sandbox.process.code_run(code, timeout=timeout))

    def upload(self, h: SandboxHandle, local_path: str, remote_path: str) -> None:
        sandbox = self._sandbox_for(h)
        upload = getattr(sandbox.fs, "upload_file", None)
        if upload is None:
            upload = sandbox.fs.upload
        upload(local_path, remote_path)

    def release(self, h: SandboxHandle) -> None:
        """Destroy a leased sandbox so candidate state is never reused."""
        with self._lock:
            sandbox = self._leased.pop(h.id, None)
        if sandbox is not None:
            error = self._delete(sandbox)
            if error is not None:
                raise RuntimeError("failed to destroy sandbox") from error
            with self._lock:
                self._tracked.pop(id(sandbox), None)
                self._remote_ids.pop(id(sandbox), None)

    def destroy_all(self) -> None:
        """Destroy every leased or pre-warmed sandbox; safe to call repeatedly."""
        self._lease_stop.set()
        lease_thread = self._lease_thread
        if lease_thread is not None and lease_thread is not threading.current_thread():
            lease_thread.join(timeout=1)
        self._lease_thread = None
        with self._lock:
            sandboxes = list(self._tracked.values())
            self._leased.clear()
            self._started = False
            while True:
                try:
                    self._available.get_nowait()
                except queue.Empty:
                    break
        seen: set[int] = set()
        failures = 0
        for sandbox in sandboxes:
            if id(sandbox) not in seen:
                seen.add(id(sandbox))
                error = self._delete(sandbox)
                if error is None:
                    with self._lock:
                        self._tracked.pop(id(sandbox), None)
                        self._remote_ids.pop(id(sandbox), None)
                else:
                    failures += 1
        if failures:
            raise RuntimeError(f"failed to destroy {failures} sandbox resource(s)")
