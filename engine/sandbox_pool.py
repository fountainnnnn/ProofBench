"""Daytona sandbox lifecycle manager (CONTRACTS section 7).

Sandboxes may be pre-warmed, but are never reused after a candidate has run in
them.  Destruction on release is intentional: a remote workspace cannot be
proven clean enough to cross candidate or tenant boundaries.
"""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


LOGGER = logging.getLogger("proofbench.sandbox_pool")
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


def resolve_gpu_type(name: str):
    """The SDK's GpuType member for a configured name, or None to let it pick.

    The SDK rejects a bare string, so a name that does not match a member is
    dropped rather than passed through as one.
    """
    if not name:
        return None
    try:
        from daytona.common.sandbox import GpuType
    except Exception:
        return None
    wanted = str(name).replace("_", "-").upper()
    for member in GpuType:
        if str(getattr(member, "value", "")).replace("_", "-").upper() == wanted:
            return member
    return None


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
        self.max_concurrent = 0  # set below, once the memory shape is known
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
        try:
            configured_memory = int(
                os.environ.get("PROOFBENCH_SANDBOX_MEMORY_GIB", "8")
            )
        except ValueError:
            configured_memory = 8
        try:
            configured_cpu = int(os.environ.get("PROOFBENCH_SANDBOX_CPU", "4"))
        except ValueError:
            configured_cpu = 4
        # Documents are sharded across processes inside a sandbox, so the shape
        # decides how much of a dataset runs at once: each worker holds its own
        # copy of the candidate's model. Four CPUs and 8GiB is the baseline that
        # lets a local OCR runtime use all of them without being killed.
        self.memory_gib = min(64, max(4, configured_memory))
        self.cpu = min(16, max(2, configured_cpu))
        # A GPU is supported but off, and the reason is measured, not assumed.
        #
        # Daytona's snapshot builders have no GPU runners ("No available runners
        # with GPU type: RTX-4090", same for H100 and for an unpinned request),
        # and a sandbox created from a snapshot cannot override its resources.
        # So an accelerator can only be had by building the candidate's image at
        # sandbox-creation time, which costs 62s+ per sandbox EVERY run and is
        # not cached between runs.
        #
        # That trade loses. EasyOCR is the candidate a GPU helps most: 12.5s per
        # document on CPU against 0.60s on an RTX-4090, so 15 documents go from
        # ~187s to ~14s. But the prebuilt snapshot starts in ~1s while the GPU
        # path pays a fresh image build first, and for the lighter candidates
        # (tesseract 13s, openai_vision 15s of total work) the build alone costs
        # more than the whole candidate.
        #
        # Turn this on when the provider offers GPU snapshot runners, or when a
        # prebuilt image can be pulled from a registry instead of built. The
        # adapters already detect their device at runtime, so nothing else has
        # to change.
        self.gpu = max(0, int(os.environ.get("PROOFBENCH_SANDBOX_GPU", "0") or 0))
        # Enough for a CUDA-sized install, small enough that a full pool fits
        # inside the account's disk quota many times over.
        try:
            self.disk_gib = min(100, max(5, int(
                os.environ.get("PROOFBENCH_SANDBOX_DISK_GIB", "10"))))
        except ValueError:
            self.disk_gib = 10
        self.gpu_type = str(
            os.environ.get("PROOFBENCH_SANDBOX_GPU_TYPE", "RTX-4090") or ""
        ).strip()
        # Providers cap total concurrent memory per account, not sandbox count.
        # Warming more sandboxes than that budget allows fails the excess with
        # "Total memory limit exceeded" and drops otherwise-runnable candidates
        # to documentation-only scoring, so the pool never asks for more than
        # the budget divides into.
        try:
            budget_gib = int(os.environ.get("PROOFBENCH_SANDBOX_MEMORY_BUDGET_GIB", "0"))
        except ValueError:
            budget_gib = 0
        self.max_concurrent = (
            max(1, budget_gib // self.memory_gib) if budget_gib > 0 else 0
        )
        # The budget must bound EVERY live sandbox, not just the pre-warm size:
        # acquire() creates on demand, and a pipeline wider than the budget
        # would otherwise ask the provider for memory it will refuse. Waiting
        # here turns that refusal into queueing behind a peer's release.
        self._capacity = (
            threading.BoundedSemaphore(self.max_concurrent) if self.max_concurrent else None
        )
        self.size = size
        default_ledger = (
            Path(__file__).resolve().parent.parent / "runs" / "sandbox_ledger.sqlite3"
        )
        self._ledger = _SandboxLedger(
            ledger_path
            or os.environ.get("PROOFBENCH_SANDBOX_LEDGER", str(default_ledger)),
            deployment
            or os.environ.get("PROOFBENCH_DEPLOYMENT_ID", "local"),
        )

    @property
    def size(self) -> int:
        return self._size

    @size.setter
    def size(self, value: int) -> None:
        """Clamp every assignment to the account's concurrent-memory budget.

        Callers size the pool from the candidate count, so clamping only in
        ``__init__`` would be undone by the next assignment.
        """
        requested = max(1, int(value))
        cap = getattr(self, "max_concurrent", 0)
        self._size = min(requested, cap) if cap else requested

    def _client(self):
        if self._daytona is None:
            from daytona import Daytona

            self._daytona = Daytona()  # configured from DAYTONA_* env vars
        return self._daytona

    # Providers meter total concurrent sandbox memory per account, and their
    # accounting lags deletion: a run started right after another can be told
    # "Total memory limit exceeded" while the previous run's sandboxes are
    # still tearing down. That is congestion, not a configuration fault, so a
    # bounded retry outlasts the teardown instead of failing the candidate.
    CAPACITY_RETRY_ATTEMPTS = 5
    CAPACITY_RETRY_SECONDS = 10.0

    # A GPU is an optimisation, never a requirement. Accelerator capacity is
    # finite and regional: when the provider cannot give this run one, the run
    # proceeds on CPU rather than failing, and says so once.
    _GPU_UNAVAILABLE_MARKERS = (
        "gpu", "accelerator", "no capacity", "not available", "insufficient",
    )

    def _is_gpu_capacity_error(self, exc) -> bool:
        message = str(exc).casefold()
        return bool(self.gpu) and any(
            marker in message for marker in self._GPU_UNAVAILABLE_MARKERS)

    def _create_with_capacity_retry(self, create_params, register: bool = False):
        last_error = None
        for attempt in range(self.CAPACITY_RETRY_ATTEMPTS):
            if attempt:
                time.sleep(self.CAPACITY_RETRY_SECONDS)
            try:
                sandbox = self._client().create(create_params, timeout=300)
                return self._register_created(sandbox) if register else sandbox
            except Exception as exc:  # provider SDK error taxonomy is theirs
                message = str(exc).lower()
                if "memory limit exceeded" not in message and "limit exceeded" not in message:
                    raise
                last_error = exc
        raise last_error

    def _resources(self):
        """The shape every candidate sandbox is created with, GPU included.

        Disk is stated rather than left to the provider. A GPU request without
        one reserved enough per sandbox to exhaust the account's 300GiB disk
        quota after a handful of runs, and the failure reads as "Total disk
        limit exceeded" on the NEXT run, which points at storage rather than at
        the shape that caused it.
        """
        from daytona.common.sandbox import Resources

        if not self.gpu:
            return Resources(cpu=self.cpu, memory=self.memory_gib,
                             disk=self.disk_gib)
        return Resources(cpu=self.cpu, memory=self.memory_gib,
                         disk=self.disk_gib, gpu=self.gpu,
                         gpu_type=resolve_gpu_type(self.gpu_type))

    def _create_one(self, snapshot: str | None = None):
        if snapshot:
            from daytona import CreateSandboxFromSnapshotParams

            # A snapshot already carries the candidate's dependencies and its own
            # resource shape, so no image build and no Resources override.
            return self._create_with_capacity_retry(
                CreateSandboxFromSnapshotParams(snapshot=snapshot, language="python",
                                                auto_delete_interval=0),
                register=True)
        try:
            from daytona import CreateSandboxFromImageParams
            from daytona.common.sandbox import Resources

            create_params = CreateSandboxFromImageParams(
                image=self.image,
                language="python",
                resources=self._resources(),
                # A GPU sandbox must be ephemeral. Harmless without one, and
                # ProofBench destroys every sandbox after its candidate anyway.
                auto_delete_interval=0,
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
            create_params = {
                "image": self.image,
                "language": "python",
                "resources": {"cpu": self.cpu, "memory": self.memory_gib,
                              "gpu": self.gpu},
            }

        try:
            sandbox = self._create_with_capacity_retry(create_params)
        except Exception as exc:
            if not self._is_gpu_capacity_error(exc):
                raise
            # Drop the accelerator for the rest of this pool's life so every
            # candidate still runs on identical hardware; a run where some
            # candidates had a GPU and others did not would make the latency
            # column incomparable, which is worse than being uniformly slower.
            LOGGER.info("gpu unavailable, continuing on cpu: %s", type(exc).__name__)
            self.gpu = 0
            create_params = CreateSandboxFromImageParams(
                image=self.image, language="python",
                resources=self._resources(), auto_delete_interval=0,
            )
            sandbox = self._create_with_capacity_retry(create_params)
        return self._register_created(sandbox)

    def _register_created(self, sandbox):
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

    def _create_with_slot(self):
        """One creation charged against the capacity budget (when one is set)."""
        if self._capacity is None:
            return self._create_one()
        if not self._capacity.acquire(timeout=1200):
            raise RuntimeError("sandbox capacity wait timed out")
        try:
            return self._create_one()
        except Exception:
            self._capacity.release()
            raise

    def preflight(self) -> None:
        """Create one sandbox eagerly, letting provider errors propagate.

        ``start`` swallows creation failures because ``acquire`` recreates on
        demand, so an account that cannot create sandboxes at all is only
        discovered after the caller has already paid for documentation
        intelligence and adapter generation for every candidate. This surfaces
        that rejection first. The probe is kept in the available queue and
        leased by the first candidate, so the check provisions nothing extra.
        """
        # A subclass that owns its own handle lifecycle (the offline test's
        # local pool, an injected fake) creates nothing remotely, so probing
        # the provider would fail on a machine that has no credentials and
        # never needed any. Same test `_sandbox_for` uses to decide whether a
        # handle is base-pool managed.
        if type(self).acquire is not SandboxPool.acquire:
            return
        with self._lock:
            if self._started or not self._available.empty():
                return
        self._available.put(self._create_with_slot())

    def start(self) -> None:
        """Pre-warm ``size`` clean sandboxes in parallel."""
        with self._lock:
            if self._started:
                return
            self._started = True

        def warm():
            try:
                self._available.put(self._create_with_slot())
            except Exception:
                # acquire() creates on demand and will surface provider errors.
                pass

        # A preflight probe already sits in the queue; warm the remainder only.
        warm_count = max(0, self.size - self._available.qsize())
        threads = [threading.Thread(target=warm, daemon=True) for _ in range(warm_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self._start_lease_heartbeat()

    def acquire(self, label: str, snapshot: str | None = None) -> SandboxHandle:
        """Lease a clean sandbox and return an opaque, one-use handle.

        ``snapshot`` asks for a sandbox built from a specific prebuilt image, so
        a pre-warmed generic sandbox cannot satisfy it.
        """
        try:
            if snapshot:
                raise queue.Empty
            sandbox = self._available.get_nowait()
        except queue.Empty:
            if self._capacity is not None:
                # Wait for a peer to finish rather than ask the provider for
                # memory beyond the account budget. Bounded: a full pipeline
                # stage takes minutes, not the 20 the timeout allows.
                if not self._capacity.acquire(timeout=1200):
                    raise RuntimeError(
                        "sandbox capacity wait timed out; the memory budget "
                        "never freed a slot")
                try:
                    sandbox = self._create_one(snapshot) if snapshot else self._create_one()
                except Exception:
                    self._capacity.release()
                    raise
            else:
                sandbox = self._create_one(snapshot) if snapshot else self._create_one()
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

    def exec(self, h: SandboxHandle, cmd: str, timeout: int = 120,
             on_line=None) -> str:
        """Run one command and return its complete output.

        ``on_line`` receives every line once the command finishes. It exists so
        callers can report output line by line without knowing how it was
        obtained, and so live streaming can be switched on later without
        touching them.

        Streaming is deliberately NOT used yet. The provider does offer it, and
        it demonstrably works — a real `apt-get install` streamed 711 lines over
        61 seconds, first line at 3.3s — but only sometimes: the same code then
        returned nothing for the next command in the session, and a fresh
        session per command failed the same way non-deterministically. Two other
        traps sit behind it: a session command started with run_async never
        populates `exit_code` (so a failed build reads as a success unless the
        status is smuggled out through the log text), and the SDK's async log
        subscription never completes and ignores cancellation, hanging the
        interpreter for 300s at loop teardown. A build path that intermittently
        times out is worse than one that reports late, so this stays blocking
        until that is reliable.
        """
        sandbox = self._sandbox_for(h)
        response = sandbox.process.exec(cmd, timeout=timeout)
        exit_code = getattr(response, "exit_code", getattr(response, "code", None))
        text = _text(response)
        if exit_code not in (None, 0, "0"):
            raise RuntimeError(f"sandbox command failed with exit code {exit_code}: {text[-1200:]}")
        if on_line is not None:
            for line in text.splitlines():
                on_line(line)
        return text

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
            if self._capacity is not None:
                try:
                    self._capacity.release()
                except ValueError:
                    pass  # more releases than acquisitions can only mean a reset

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
