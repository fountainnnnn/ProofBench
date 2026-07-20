"""Daytona sandbox lifecycle manager (CONTRACTS §7).

A small pre-warmed pool of identical Daytona sandboxes. The orchestrator
acquires a handle per candidate, builds/runs inside it, and releases it back.
Thread-safe: the orchestrator may drive candidates in parallel.
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field


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
    def __init__(self, size: int = 4):
        self.size = size
        self._daytona = None
        self._available: queue.Queue = queue.Queue()
        self._all: list = []
        self._lock = threading.Lock()
        self._started = False

    def _client(self):
        if self._daytona is None:
            from daytona import Daytona

            self._daytona = Daytona()  # configured from DAYTONA_* env vars
        return self._daytona

    def _create_one(self):
        return self._client().create()

    def start(self) -> None:
        """Pre-warm `size` sandboxes in parallel. Safe to call once."""
        if self._started:
            return
        self._started = True

        def warm():
            try:
                self._available.put(self._create_one())
            except Exception:
                # a failed warm-up just means one fewer pre-warmed sandbox;
                # acquire() will create on demand and surface any real error
                pass

        threads = [threading.Thread(target=warm, daemon=True) for _ in range(self.size)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def acquire(self, label: str) -> SandboxHandle:
        try:
            sb = self._available.get_nowait()
        except queue.Empty:
            sb = self._create_one()
        with self._lock:
            self._all.append(sb)
        return SandboxHandle(id=f"{label}-{uuid.uuid4().hex[:8]}", label=label, sandbox=sb)

    def exec(self, h: SandboxHandle, cmd: str, timeout: int = 120) -> str:
        return _text(h.sandbox.process.exec(cmd, timeout=timeout))

    def run_python(self, h: SandboxHandle, code: str, timeout: int = 180) -> str:
        return _text(h.sandbox.process.code_run(code, timeout=timeout))

    def upload(self, h: SandboxHandle, local_path: str, remote_path: str) -> None:
        h.sandbox.fs.upload_file(local_path, remote_path)

    def release(self, h: SandboxHandle) -> None:
        """Return the sandbox to the pool for reuse (kept alive)."""
        self._available.put(h.sandbox)

    def destroy_all(self) -> None:
        with self._lock:
            sandboxes = list(self._all)
            self._all.clear()
        while True:
            try:
                sandboxes.append(self._available.get_nowait())
            except queue.Empty:
                break
        for sb in sandboxes:
            try:
                sb.delete()
            except Exception:
                pass
