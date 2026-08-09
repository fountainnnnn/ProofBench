"""PostgreSQL implementation of ProofBench's durable state contract."""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import psycopg

from server.state import SCHEMA_VERSION, SQLiteStore


# Immediate SQLite transactions serialize writers. Use the same conservative
# contract on PostgreSQL so admission, quotas, retention, and claims keep the
# exact semantics already exercised by the shared store tests.
_WRITE_LOCK_ID = 8_092_026_080_901
_SCHEMA_LOCK_ID = 8_092_026_080_902


class CompatRow(Mapping[str, Any]):
    """Row supporting both sqlite3.Row-style names and integer indexes."""

    def __init__(self, columns: tuple[str, ...], values: tuple[Any, ...]) -> None:
        self._columns = columns
        self._values = values
        self._lookup = dict(zip(columns, values, strict=True))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._lookup[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)


def _postgres_sql(sql: str) -> str:
    # Store queries are internal constants and contain no literal question
    # marks. Keeping the established qmark form makes every state operation
    # shared and testable against both backends.
    return sql.replace("?", "%s")


class CursorAdapter:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def _row(self, value):
        if value is None:
            return None
        columns = tuple(item.name for item in self._cursor.description or ())
        return CompatRow(columns, tuple(value))

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        for row in self._cursor:
            yield self._row(row)


class ConnectionAdapter:
    def __init__(self, connection) -> None:
        self._connection = connection

    def execute(self, sql: str, parameters=()) -> CursorAdapter:
        return CursorAdapter(self._connection.execute(_postgres_sql(sql), parameters))

    def executemany(self, sql: str, parameters) -> CursorAdapter:
        cursor = self._connection.cursor()
        cursor.executemany(_postgres_sql(sql), parameters)
        return CursorAdapter(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


_DDL = (
    """CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
        phase TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        spec_json TEXT, results_json TEXT, mode TEXT NOT NULL DEFAULT 'real',
        dataset_id TEXT, dataset_path TEXT, is_running INTEGER NOT NULL DEFAULT 0,
        active_kind TEXT, active_job_id TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
        event_seq INTEGER NOT NULL DEFAULT 0, event_bytes INTEGER NOT NULL DEFAULT 0,
        latest_run_id TEXT, active_worker_id TEXT, active_lease_expires_at TEXT,
        latest_job_id TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS sessions_owner_created ON sessions(owner, created_at)",
    "CREATE INDEX IF NOT EXISTS sessions_owner_running ON sessions(owner, is_running, active_kind)",
    """CREATE TABLE IF NOT EXISTS datasets (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'upload', image_count INTEGER NOT NULL DEFAULT 0,
        total_bytes BIGINT NOT NULL DEFAULT 0, reserved_bytes BIGINT NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS datasets_owner_created ON datasets(owner, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS benchmark_runs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        owner TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
        mode TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
        finished_at TEXT, spec_json TEXT, metrics_json TEXT, provenance TEXT,
        report_md TEXT, citations_json TEXT, dataset_id TEXT, worker_id TEXT,
        lease_expires_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS runs_session_created ON benchmark_runs(session_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS runs_owner_created ON benchmark_runs(owner, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS events (
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, run_id TEXT, event TEXT NOT NULL,
        data_json TEXT NOT NULL, size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(session_id, seq)
    )""",
    "CREATE INDEX IF NOT EXISTS events_session_seq ON events(session_id, seq)",
    """CREATE TABLE IF NOT EXISTS messages (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS messages_session_id ON messages(session_id, id)",
    """CREATE TABLE IF NOT EXISTS findings (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        title TEXT NOT NULL, url TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(session_id, url)
    )""",
    "CREATE INDEX IF NOT EXISTS findings_session_id ON findings(session_id, id)",
    """CREATE TABLE IF NOT EXISTS tenant_settings (
        owner TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY(owner, key)
    )""",
    """CREATE TABLE IF NOT EXISTS tenant_credentials (
        owner TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY(owner, name)
    )""",
    """CREATE TABLE IF NOT EXISTS request_usage (
        owner TEXT NOT NULL, bucket BIGINT NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY(owner, bucket)
    )""",
    """CREATE TABLE IF NOT EXISTS chat_jobs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        owner TEXT NOT NULL, dataset_id TEXT, status TEXT NOT NULL,
        created_at TEXT NOT NULL, finished_at TEXT, worker_id TEXT,
        lease_expires_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS chat_jobs_owner_created ON chat_jobs(owner, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS worker_leases (
        id TEXT PRIMARY KEY, started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS leader_leases (
        name TEXT PRIMARY KEY, owner_id TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS deletion_queue (
        id BIGSERIAL PRIMARY KEY, resource_kind TEXT NOT NULL,
        resource_id TEXT NOT NULL, path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL, last_error TEXT, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, UNIQUE(resource_kind, resource_id, path)
    )""",
    "CREATE INDEX IF NOT EXISTS deletion_queue_due ON deletion_queue(status, next_attempt_at)",
)


class PostgresStore(SQLiteStore):
    """PostgreSQL store preserving the hardened SQLiteStore method contract."""

    def __init__(self, database_url: str, *, max_events: int = 1000,
                 max_event_bytes: int = 2 * 1024 * 1024, max_messages: int = 200,
                 recover_interrupted: bool = False, worker_id: str | None = None,
                 lease_seconds: int = 90, orphan_grace_seconds: int = 120,
                 artifact_root: str | None = None) -> None:
        if not database_url.startswith(("postgres://", "postgresql://")):
            raise ValueError("ProofBench PostgreSQL URL must use postgres:// or postgresql://")
        self.database_url = database_url
        self.path = "[postgresql]"
        self.artifact_root = os.path.abspath(artifact_root or os.getcwd())
        self.max_events = max(1, int(max_events))
        self.max_event_bytes = max(1024, int(max_event_bytes))
        self.max_messages = max(1, int(max_messages))
        self.max_findings = 120
        self.worker_id = worker_id or os.environ.get("PROOFBENCH_WORKER_ID") or uuid.uuid4().hex
        self.lease_seconds = max(30, int(lease_seconds))
        self.orphan_grace_seconds = max(self.lease_seconds, int(orphan_grace_seconds))
        self._schema_lock = threading.Lock()
        self._initialize_with_retry()
        self.register_worker()
        if recover_interrupted:
            self.recover_stale_jobs()

    def _raw_connect(self):
        return psycopg.connect(
            self.database_url,
            connect_timeout=5,
            application_name="proofbench",
        )

    def connect(self) -> ConnectionAdapter:
        return ConnectionAdapter(self._raw_connect())

    @contextmanager
    def transaction(self, immediate: bool = False):
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            if immediate:
                connection.execute("SELECT pg_advisory_xact_lock(?)", (_WRITE_LOCK_ID,))
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_with_retry(self) -> None:
        for attempt in range(10):
            try:
                self.initialize()
                return
            except psycopg.OperationalError:
                if attempt == 9:
                    raise
                time.sleep(min(0.25 * (2 ** attempt), 3.0))

    def initialize(self) -> None:
        with self._schema_lock, self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(?)", (_SCHEMA_LOCK_ID,))
            connection.execute(
                "CREATE TABLE IF NOT EXISTS proofbench_schema ("
                "singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton), "
                "version INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO proofbench_schema(singleton,version) VALUES(TRUE,0) "
                "ON CONFLICT(singleton) DO NOTHING"
            )
            version = connection.execute(
                "SELECT version FROM proofbench_schema WHERE singleton=TRUE FOR UPDATE"
            ).fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"unsupported ProofBench state schema version {version}")
            for statement in _DDL:
                connection.execute(statement)
            connection.execute(
                "UPDATE proofbench_schema SET version=? WHERE singleton=TRUE",
                (SCHEMA_VERSION,),
            )
