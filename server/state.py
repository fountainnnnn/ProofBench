"""SQLite-backed durable state for sessions, immutable runs, messages, and SSE events."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


SCHEMA_VERSION = 4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace(
        "+00:00", "Z")


class BusyError(RuntimeError):
    pass


class QuotaError(RuntimeError):
    pass


class SQLiteStore:
    """Small multi-process-safe state store; each operation uses its own connection."""

    def __init__(self, path: str, *, max_events: int = 1000,
                  max_event_bytes: int = 2 * 1024 * 1024, max_messages: int = 200,
                  recover_interrupted: bool = False, worker_id: str | None = None,
                  lease_seconds: int = 90, orphan_grace_seconds: int = 120,
                  artifact_root: str | None = None) -> None:
        self.path = os.path.abspath(path)
        self.artifact_root = os.path.abspath(artifact_root or os.path.dirname(self.path))
        self.max_events = max(1, int(max_events))
        self.max_event_bytes = max(1024, int(max_event_bytes))
        self.max_messages = max(1, int(max_messages))
        self.worker_id = worker_id or os.environ.get("PROOFBENCH_WORKER_ID") or uuid.uuid4().hex
        self.lease_seconds = max(30, int(lease_seconds))
        self.orphan_grace_seconds = max(self.lease_seconds, int(orphan_grace_seconds))
        self._schema_lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for attempt in range(5):
            try:
                self.initialize()
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                # Startup is intentionally idempotent; retry the full preflight after a
                # concurrent process releases a schema/journal lock.
                time.sleep(0.05 * (attempt + 1))
        self.register_worker()
        if recover_interrupted:
            self.recover_stale_jobs()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def transaction(self, immediate: bool = False):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._schema_lock, self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"unsupported ProofBench state schema version {version}")
            preflight = connection.execute("PRAGMA quick_check").fetchone()[0]
            if preflight != "ok":
                raise RuntimeError("ProofBench state database preflight check failed")
            if version and version < SCHEMA_VERSION and os.environ.get(
                    "PROOFBENCH_MIGRATION_BACKUP", "1") == "1":
                backup = f"{self.path}.pre-v{version}-{int(time.time())}-{self.worker_id[:8]}.bak"
                with sqlite3.connect(backup) as target:
                    connection.backup(target)
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    title TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    spec_json TEXT,
                    results_json TEXT,
                    -- ProofBench is real-only. The column is retained so runs
                    -- persisted by earlier versions stay readable and can still
                    -- be presented as historical synthetic evidence.
                    mode TEXT NOT NULL DEFAULT 'real',
                    dataset_id TEXT,
                    dataset_path TEXT,
                    is_running INTEGER NOT NULL DEFAULT 0,
                    active_kind TEXT,
                    active_job_id TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    event_seq INTEGER NOT NULL DEFAULT 0,
                    event_bytes INTEGER NOT NULL DEFAULT 0,
                    latest_run_id TEXT,
                    active_worker_id TEXT,
                    active_lease_expires_at TEXT,
                    latest_job_id TEXT
                );
                CREATE INDEX IF NOT EXISTS sessions_owner_created
                    ON sessions(owner, created_at);
                CREATE INDEX IF NOT EXISTS sessions_owner_running
                    ON sessions(owner, is_running, active_kind);

                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'upload',
                    image_count INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    reserved_bytes INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS datasets_owner_created ON datasets(owner, created_at DESC);

                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    spec_json TEXT,
                    metrics_json TEXT,
                    provenance TEXT,
                    report_md TEXT,
                    citations_json TEXT,
                    dataset_id TEXT,
                    worker_id TEXT,
                    lease_expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS runs_session_created
                    ON benchmark_runs(session_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS runs_owner_created
                    ON benchmark_runs(owner, created_at DESC);

                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    run_id TEXT,
                    event TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, seq)
                );
                CREATE INDEX IF NOT EXISTS events_session_seq ON events(session_id, seq);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session_id ON messages(session_id, id);

                CREATE TABLE IF NOT EXISTS request_usage (
                    owner TEXT NOT NULL,
                    bucket INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY(owner, bucket)
                );
                CREATE TABLE IF NOT EXISTS chat_jobs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    owner TEXT NOT NULL,
                    dataset_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    worker_id TEXT,
                    lease_expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS chat_jobs_owner_created
                    ON chat_jobs(owner, created_at DESC);
                CREATE TABLE IF NOT EXISTS worker_leases (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leader_leases (
                    name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deletion_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(resource_kind, resource_id, path)
                );
                CREATE INDEX IF NOT EXISTS deletion_queue_due
                    ON deletion_queue(status, next_attempt_at);
            """)
            # Serialize migrations across processes. Re-reading the version after the write
            # lock is essential: another worker may have completed the upgrade while this
            # worker was creating idempotent tables above.
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise RuntimeError(f"unsupported ProofBench state schema version {version}")
                if version == 1:
                    self._migrate_v1(connection)
                    version = 2
                if version == 2:
                    self._migrate_v2(connection)
                    version = 3
                if version == 3:
                    self._migrate_v3(connection)
                    version = 4
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError("ProofBench state database integrity check failed")

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

    def _add_columns(self, connection: sqlite3.Connection, table: str,
                     definitions: dict[str, str]) -> None:
        existing = self._columns(connection, table)
        for name, definition in definitions.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate_v1(self, connection: sqlite3.Connection) -> None:
        self._add_columns(connection, "sessions", {
            "active_worker_id": "TEXT", "active_lease_expires_at": "TEXT",
            "latest_job_id": "TEXT",
        })
        self._add_columns(connection, "datasets", {
            "kind": "TEXT NOT NULL DEFAULT 'upload'",
            "image_count": "INTEGER NOT NULL DEFAULT 0",
            "total_bytes": "INTEGER NOT NULL DEFAULT 0",
            "reserved_bytes": "INTEGER NOT NULL DEFAULT 0",
        })
        self._add_columns(connection, "benchmark_runs", {
            "dataset_id": "TEXT", "worker_id": "TEXT", "lease_expires_at": "TEXT",
        })

    def _migrate_v2(self, connection: sqlite3.Connection) -> None:
        # Version 3 formalizes durable deletion and lease tables. CREATE TABLE IF NOT EXISTS
        # above is intentionally idempotent so a partially initialized database can recover.
        self._migrate_v1(connection)

    def _migrate_v3(self, connection: sqlite3.Connection) -> None:
        # Provenance is authoritative state, not metadata inferred from an optional
        # artifact file. Legacy rows remain NULL and are reported as unverified.
        self._add_columns(connection, "benchmark_runs", {"provenance": "TEXT"})

    @staticmethod
    def _json(value) -> str | None:
        return None if value is None else json.dumps(value, separators=(",", ":"))

    @staticmethod
    def _decode(value, default=None):
        if value is None:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    def register_worker(self) -> None:
        now = utc_now()
        expires = utc_after(self.lease_seconds)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO worker_leases(id,started_at,heartbeat_at,expires_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,"
                "expires_at=excluded.expires_at",
                (self.worker_id, now, now, expires),
            )

    def heartbeat_worker(self) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE worker_leases SET heartbeat_at=?,expires_at=? WHERE id=?",
                (now, utc_after(self.lease_seconds), self.worker_id),
            )

    def acquire_leader(self, name: str, ttl_seconds: int | None = None) -> bool:
        ttl = max(30, int(ttl_seconds or self.lease_seconds))
        now = utc_now()
        expires = utc_after(ttl)
        with self.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT owner_id,expires_at FROM leader_leases WHERE name=?", (name,)
            ).fetchone()
            if current and current["owner_id"] != self.worker_id and current["expires_at"] > now:
                return False
            connection.execute(
                "INSERT INTO leader_leases(name,owner_id,heartbeat_at,expires_at) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET owner_id=excluded.owner_id,"
                "heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at",
                (name, self.worker_id, now, expires),
            )
            return True

    def heartbeat_job(self, session_id: str, job_id: str) -> bool:
        expires = utc_after(self.lease_seconds)
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            result = connection.execute(
                "UPDATE sessions SET active_lease_expires_at=?,updated_at=? "
                "WHERE id=? AND active_job_id=? AND active_worker_id=? AND is_running=1",
                (expires, now, session_id, job_id, self.worker_id),
            )
            connection.execute(
                "UPDATE benchmark_runs SET lease_expires_at=? WHERE id=? AND worker_id=?",
                (expires, job_id, self.worker_id),
            )
            connection.execute(
                "UPDATE chat_jobs SET lease_expires_at=? WHERE id=? AND worker_id=?",
                (expires, job_id, self.worker_id),
            )
            return result.rowcount == 1

    def active_job_contract(self) -> dict:
        now = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT active_job_id,active_worker_id,active_lease_expires_at FROM sessions "
                "WHERE is_running=1 AND active_job_id IS NOT NULL AND active_lease_expires_at>?",
                (now,),
            ).fetchall()
        return {
            "active_owner_keys": [row["active_job_id"] for row in rows],
            "owners": [dict(row) for row in rows],
            "orphan_before": (datetime.now(timezone.utc) - timedelta(
                seconds=self.orphan_grace_seconds)).isoformat().replace("+00:00", "Z"),
        }
    def create_session(self, owner: str, title: str, dataset_id: str | None = None) -> dict:
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            for _ in range(100):
                session_id = uuid.uuid4().hex[:12]
                try:
                    connection.execute(
                        "INSERT INTO sessions(id,owner,title,phase,created_at,updated_at,dataset_id) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (session_id, owner, title, "INTAKE", now, now, dataset_id),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise RuntimeError("could not allocate a session id")
        return self.get_session(session_id, owner)

    def _session_row(self, session_id: str, owner: str | None = None):
        with self.connect() as connection:
            if owner is None:
                return connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            return connection.execute("SELECT * FROM sessions WHERE id=? AND owner=?",
                                      (session_id, owner)).fetchone()

    def session_owner(self, session_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT owner FROM sessions WHERE id=?", (session_id,)).fetchone()
        return row[0] if row else None

    def get_session(self, session_id: str, owner: str | None = None) -> dict | None:
        row = self._session_row(session_id, owner)
        if row is None:
            return None
        with self.connect() as connection:
            messages = connection.execute(
                "SELECT role,text,created_at FROM messages WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()
            events = connection.execute(
                "SELECT seq,event,data_json FROM events WHERE session_id=? ORDER BY seq", (session_id,)
            ).fetchall()
            history = connection.execute(
                "SELECT id,status,phase,mode,created_at,finished_at FROM benchmark_runs "
                "WHERE session_id=? ORDER BY created_at DESC", (session_id,)
            ).fetchall()
        return {
            "id": row["id"], "owner": row["owner"], "title": row["title"],
            "phase": row["phase"], "created_at": row["created_at"],
            "spec": self._decode(row["spec_json"]), "results": self._decode(row["results_json"]),
            "mode": row["mode"], "dataset_id": row["dataset_id"],
            "dataset_path": row["dataset_path"], "is_running": bool(row["is_running"]),
            "latest_run_id": row["latest_run_id"],
            "latest_job_id": row["latest_job_id"],
            "active_job_id": row["active_job_id"], "active_kind": row["active_kind"],
            "active_worker_id": row["active_worker_id"],
            "active_lease_expires_at": row["active_lease_expires_at"],
            "run_history": [dict(item) for item in history],
            "messages": [dict(item) for item in messages],
            "events": [(item["seq"], item["event"], self._decode(item["data_json"], {}))
                       for item in events],
            "event_seq": row["event_seq"],
        }

    def list_sessions(self, owner: str) -> list[dict]:
        with self.connect() as connection:
            # The latest run is joined in because `sessions.mode` is not evidence:
            # it defaults to 'real' at creation and says nothing about whether a
            # measured execution ever happened. Only benchmark_runs carries the
            # immutable provenance marker and a terminal status.
            rows = connection.execute(
                "SELECT s.id,s.title,s.phase,s.mode,s.is_running,s.created_at,"
                "s.latest_run_id,r.status AS latest_run_status,"
                "r.provenance AS latest_run_provenance,"
                "(r.metrics_json IS NOT NULL) AS latest_run_has_metrics "
                "FROM sessions s LEFT JOIN benchmark_runs r ON r.id=s.latest_run_id "
                "WHERE s.owner=? ORDER BY s.created_at", (owner,)
            ).fetchall()
        return [
            {
                **dict(row),
                "is_running": bool(row["is_running"]),
                "latest_run_has_metrics": bool(row["latest_run_has_metrics"]),
            }
            for row in rows
        ]

    def set_value(self, session_id: str, key: str, value) -> bool:
        columns = {"title": "title", "phase": "phase", "spec": "spec_json",
                   "results": "results_json", "mode": "mode", "dataset_id": "dataset_id",
                   "dataset_path": "dataset_path"}
        column = columns.get(key)
        if not column:
            raise ValueError("protected or unknown session field")
        stored = self._json(value) if column.endswith("_json") else value
        with self.connect() as connection:
            result = connection.execute(
                f"UPDATE sessions SET {column}=?,updated_at=? WHERE id=?",
                (stored, utc_now(), session_id),
            )
            return result.rowcount == 1

    def register_dataset(self, dataset_id: str, owner: str, path: str, *,
                         kind: str = "upload", image_count: int = 0,
                         total_bytes: int = 0) -> None:
        with self.transaction(immediate=True) as connection:
            existing = connection.execute("SELECT owner,path,status FROM datasets WHERE id=?",
                                          (dataset_id,)).fetchone()
            if existing and (existing["owner"] != owner or existing["path"] != path):
                raise RuntimeError("dataset id ownership conflict")
            if existing and existing["status"] != "active":
                raise BusyError("dataset deletion is in progress")
            connection.execute(
                "INSERT INTO datasets(id,owner,path,status,created_at,kind,image_count,total_bytes,reserved_bytes) "
                "VALUES(?,?,?,'active',?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET "
                "status='active',kind=excluded.kind,image_count=excluded.image_count,"
                "total_bytes=excluded.total_bytes,reserved_bytes=0",
                (dataset_id, owner, path, utc_now(), kind, max(0, image_count), max(0, total_bytes)),
            )

    def reserve_dataset(self, owner: str, path_for_id, total_bytes: int,
                        tenant_quota_bytes: int) -> dict:
        requested = max(0, int(total_bytes))
        with self.transaction(immediate=True) as connection:
            used = connection.execute(
                "SELECT COALESCE(SUM(total_bytes+reserved_bytes),0) FROM datasets "
                "WHERE owner=? AND status IN ('active','reserved','deleting')", (owner,)
            ).fetchone()[0]
            if tenant_quota_bytes > 0 and used + requested > tenant_quota_bytes:
                raise QuotaError("tenant dataset storage quota reached")
            for _ in range(100):
                dataset_id = uuid.uuid4().hex[:12]
                path = path_for_id(dataset_id)
                try:
                    connection.execute(
                        "INSERT INTO datasets(id,owner,path,status,created_at,kind,image_count,"
                        "total_bytes,reserved_bytes) VALUES(?,?,?,'reserved',?,'upload',0,0,?)",
                        (dataset_id, owner, path, utc_now(), requested),
                    )
                    return {"id": dataset_id, "owner": owner, "path": path,
                            "reserved_bytes": requested}
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("could not allocate a dataset id")

    def activate_dataset(self, dataset_id: str, owner: str, *, image_count: int,
                         total_bytes: int) -> dict:
        with self.transaction(immediate=True) as connection:
            result = connection.execute(
                "UPDATE datasets SET status='active',kind='upload',image_count=?,total_bytes=?,"
                "reserved_bytes=0 WHERE id=? AND owner=? AND status='reserved'",
                (max(0, image_count), max(0, total_bytes), dataset_id, owner),
            )
            if result.rowcount != 1:
                raise KeyError(dataset_id)
            row = connection.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            return dict(row)

    def release_dataset_reservation(self, dataset_id: str, owner: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM datasets WHERE id=? AND owner=? AND status='reserved'",
                (dataset_id, owner),
            )

    def release_stale_dataset_reservations(self, max_age_seconds: int = 3600) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(60, max_age_seconds))).isoformat().replace(
            "+00:00", "Z")
        with self.transaction(immediate=True) as connection:
            rows = connection.execute(
                "SELECT id,path FROM datasets WHERE status='reserved' AND created_at<?", (cutoff,)
            ).fetchall()
            for row in rows:
                self.enqueue_deletion("dataset_reservation", row["id"], row["path"], connection)
            connection.execute("DELETE FROM datasets WHERE status='reserved' AND created_at<?", (cutoff,))
            return len(rows)

    def get_dataset(self, dataset_id: str, owner: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id,owner,path,status,created_at,kind,image_count,total_bytes "
                "FROM datasets WHERE id=? AND owner=? AND status='active'",
                (dataset_id, owner),
            ).fetchone()
        return dict(row) if row else None

    def get_deleting_dataset(self, dataset_id: str, owner: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id,owner,path,status,created_at,kind,image_count,total_bytes "
                "FROM datasets WHERE id=? AND owner=? AND status='deleting'",
                (dataset_id, owner),
            ).fetchone()
        return dict(row) if row else None

    def bind_dataset(self, session_id: str, owner: str, dataset_id: str, path: str) -> None:
        with self.transaction(immediate=True) as connection:
            dataset = connection.execute(
                "SELECT path FROM datasets WHERE id=? AND owner=? AND status='active'",
                (dataset_id, owner),
            ).fetchone()
            if not dataset or dataset["path"] != path:
                raise KeyError(dataset_id)
            result = connection.execute(
                "UPDATE sessions SET dataset_id=?,dataset_path=?,updated_at=? WHERE id=? AND owner=?",
                (dataset_id, path, utc_now(), session_id, owner),
            )
            if result.rowcount != 1:
                raise KeyError(session_id)

    def list_datasets(self, owner: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,created_at,kind,image_count,total_bytes FROM datasets "
                "WHERE owner=? AND status='active' "
                "ORDER BY created_at DESC", (owner,)
            ).fetchall()
        return [dict(row) for row in rows]

    def begin_dataset_delete(self, dataset_id: str, owner: str) -> dict | None:
        with self.transaction(immediate=True) as connection:
            dataset = connection.execute(
                "SELECT id,path FROM datasets WHERE id=? AND owner=? AND status='active'",
                (dataset_id, owner),
            ).fetchone()
            if not dataset:
                return None
            references = connection.execute(
                "SELECT (SELECT COUNT(*) FROM sessions WHERE owner=? AND dataset_id=?) + "
                "(SELECT COUNT(*) FROM benchmark_runs WHERE owner=? AND dataset_id=?)",
                (owner, dataset_id, owner, dataset_id),
            ).fetchone()[0]
            if references:
                raise BusyError("dataset is referenced by a session")
            connection.execute("UPDATE datasets SET status='deleting' WHERE id=?", (dataset_id,))
            self.enqueue_deletion("dataset", dataset_id, dataset["path"], connection)
            return dict(dataset)

    def finish_dataset_delete(self, dataset_id: str, success: bool) -> None:
        with self.connect() as connection:
            if success:
                connection.execute("DELETE FROM datasets WHERE id=? AND status='deleting'", (dataset_id,))
                connection.execute(
                    "UPDATE deletion_queue SET status='done',updated_at=? "
                    "WHERE resource_kind='dataset' AND resource_id=?",
                    (utc_now(), dataset_id),
                )
            else:
                connection.execute(
                    "UPDATE deletion_queue SET attempts=attempts+1,last_error='filesystem_delete_failed',"
                    "next_attempt_at=?,updated_at=? WHERE resource_kind='dataset' AND resource_id=?",
                    (utc_after(2), utc_now(), dataset_id),
                )

    def add_message(self, session_id: str, role: str, text: str) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute("INSERT INTO messages(session_id,role,text,created_at) VALUES(?,?,?,?)",
                               (session_id, role, text, utc_now()))
            connection.execute(
                "DELETE FROM messages WHERE session_id=? AND id NOT IN "
                "(SELECT id FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                (session_id, session_id, self.max_messages),
            )

    def claim_chat(self, session_id: str) -> bool:
        now = utc_now()
        job_id = uuid.uuid4().hex
        with self.transaction(immediate=True) as connection:
            result = connection.execute(
                "UPDATE sessions SET is_running=1,active_kind='chat',active_job_id=?,"
                "active_worker_id=?,active_lease_expires_at=?,latest_job_id=?,"
                "cancel_requested=0,updated_at=? WHERE id=? AND is_running=0",
                (job_id, self.worker_id, utc_after(self.lease_seconds), job_id, now, session_id),
            )
            if result.rowcount:
                owner = connection.execute("SELECT owner FROM sessions WHERE id=?", (session_id,)).fetchone()[0]
                connection.execute(
                    "INSERT INTO chat_jobs(id,session_id,owner,status,created_at,worker_id,lease_expires_at) "
                    "VALUES(?,?,?,'running',?,?,?)",
                    (job_id, session_id, owner, now, self.worker_id, utc_after(self.lease_seconds)),
                )
            return result.rowcount == 1

    def claim_chat_atomic(self, session_id: str, owner: str, dataset_id: str,
                          mode: str, message: str, max_concurrent: int,
                          daily_limit: int) -> dict:
        now = utc_now()
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                       microsecond=0).isoformat().replace("+00:00", "Z")
        job_id = uuid.uuid4().hex
        expires = utc_after(self.lease_seconds)
        with self.transaction(immediate=True) as connection:
            session = connection.execute(
                "SELECT is_running FROM sessions WHERE id=? AND owner=?", (session_id, owner)
            ).fetchone()
            if not session:
                raise KeyError(session_id)
            if session["is_running"]:
                raise BusyError("session already working")
            dataset = connection.execute(
                "SELECT path FROM datasets WHERE id=? AND owner=? AND status='active'",
                (dataset_id, owner),
            ).fetchone()
            if not dataset:
                raise KeyError(dataset_id)
            active = connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE owner=? AND is_running=1 AND active_kind='chat'",
                (owner,),
            ).fetchone()[0]
            if max_concurrent > 0 and active >= max_concurrent:
                raise QuotaError("tenant concurrent chat limit reached")
            daily = connection.execute(
                "SELECT COUNT(*) FROM chat_jobs WHERE owner=? AND created_at>=?", (owner, day_start)
            ).fetchone()[0]
            if daily_limit > 0 and daily >= daily_limit:
                raise QuotaError("tenant daily chat quota reached")
            connection.execute(
                "INSERT INTO chat_jobs(id,session_id,owner,dataset_id,status,created_at,worker_id,lease_expires_at) "
                "VALUES(?,?,?,?,'running',?,?,?)",
                (job_id, session_id, owner, dataset_id, now, self.worker_id, expires),
            )
            connection.execute(
                "UPDATE sessions SET is_running=1,active_kind='chat',active_job_id=?,"
                "active_worker_id=?,active_lease_expires_at=?,latest_job_id=?,cancel_requested=0,"
                "dataset_id=?,dataset_path=?,mode=?,updated_at=? WHERE id=?",
                (job_id, self.worker_id, expires, job_id, dataset_id, dataset["path"], mode,
                 now, session_id),
            )
            connection.execute(
                "INSERT INTO messages(session_id,role,text,created_at) VALUES(?,?,?,?)",
                (session_id, "user", message, now),
            )
            connection.execute(
                "DELETE FROM messages WHERE session_id=? AND id NOT IN "
                "(SELECT id FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                (session_id, session_id, self.max_messages),
            )
        return {"id": job_id, "session_id": session_id, "owner": owner,
                "status": "running", "kind": "chat"}

    def claim_benchmark(self, session_id: str, owner: str, spec: dict, mode: str,
                        max_concurrent: int, daily_limit: int,
                        dataset_id: str | None = None) -> dict:
        now = utc_now()
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                       microsecond=0).isoformat().replace("+00:00", "Z")
        with self.transaction(immediate=True) as connection:
            session = connection.execute(
                "SELECT id FROM sessions WHERE id=? AND owner=?", (session_id, owner)
            ).fetchone()
            if not session:
                raise KeyError(session_id)
            if connection.execute("SELECT is_running FROM sessions WHERE id=?", (session_id,)).fetchone()[0]:
                raise BusyError("session already working")
            active = connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE owner=? AND is_running=1 AND active_kind='benchmark'",
                (owner,),
            ).fetchone()[0]
            if max_concurrent > 0 and active >= max_concurrent:
                raise QuotaError("tenant concurrent run limit reached")
            daily = connection.execute(
                "SELECT COUNT(*) FROM benchmark_runs WHERE owner=? AND created_at>=?", (owner, day_start)
            ).fetchone()[0]
            if daily_limit > 0 and daily >= daily_limit:
                raise QuotaError("tenant daily run quota reached")
            dataset_path = None
            if dataset_id:
                dataset = connection.execute(
                    "SELECT path FROM datasets WHERE id=? AND owner=? AND status='active'",
                    (dataset_id, owner),
                ).fetchone()
                if not dataset:
                    raise KeyError(dataset_id)
                dataset_path = dataset["path"]
            for _ in range(100):
                run_id = uuid.uuid4().hex[:12]
                try:
                    connection.execute(
                        "INSERT INTO benchmark_runs(id,session_id,owner,status,phase,mode,created_at,started_at,"
                        "spec_json,dataset_id,worker_id,lease_expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, session_id, owner, "running", "PROVISIONING", mode, now, now,
                         self._json(spec), dataset_id, self.worker_id, utc_after(self.lease_seconds)),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise RuntimeError("could not allocate a run id")
            connection.execute(
                "UPDATE sessions SET is_running=1,active_kind='benchmark',active_job_id=?,"
                "active_worker_id=?,active_lease_expires_at=?,latest_job_id=?,"
                "cancel_requested=0,latest_run_id=?,spec_json=?,mode=?,dataset_id=COALESCE(?,dataset_id),"
                "dataset_path=COALESCE(?,dataset_path),updated_at=? WHERE id=?",
                (run_id, self.worker_id, utc_after(self.lease_seconds), run_id, run_id,
                 self._json(spec), mode, dataset_id, dataset_path, now, session_id),
            )
        return {"id": run_id, "session_id": session_id, "owner": owner,
                "status": "running", "phase": "PROVISIONING", "mode": mode,
                "created_at": now}

    def request_stop(self, session_id: str) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE sessions SET cancel_requested=1,updated_at=? WHERE id=? AND is_running=1",
                (utc_now(), session_id),
            )
            return result.rowcount == 1

    def is_cancelled(self, session_id: str) -> bool:
        row = self._session_row(session_id)
        return bool(row and row["cancel_requested"])

    def finish(self, session_id: str, *, run_id: str | None = None,
               job_id: str | None = None, cancelled: bool = False,
               failed: bool = False) -> None:
        phase = "STOPPED" if cancelled else "FAILED" if failed else None
        status = "stopped" if cancelled else "failed" if failed else "completed"
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            if run_id:
                connection.execute(
                    "UPDATE benchmark_runs SET status=?,phase=COALESCE(?,phase),finished_at=? "
                    "WHERE id=? AND session_id=? AND status='running'",
                    (status, phase, now, run_id, session_id),
                )
                condition = "active_job_id=?"
                params = (run_id,)
            else:
                target_job_id = job_id
                if target_job_id is None:
                    active = connection.execute(
                        "SELECT active_job_id FROM sessions WHERE id=? AND active_kind='chat'",
                        (session_id,),
                    ).fetchone()
                    target_job_id = active["active_job_id"] if active else None
                if target_job_id:
                    connection.execute(
                        "UPDATE chat_jobs SET status=?,finished_at=? WHERE id=? AND session_id=? "
                        "AND status='running'",
                        (status, now, target_job_id, session_id),
                    )
                    condition = "active_kind='chat' AND active_job_id=?"
                    params = (target_job_id,)
                else:
                    condition = "active_kind='chat'"
                    params = ()
            connection.execute(
                f"UPDATE sessions SET is_running=0,active_kind=NULL,active_job_id=NULL,"
                f"active_worker_id=NULL,active_lease_expires_at=NULL,"
                f"cancel_requested=0,phase=COALESCE(?,phase),updated_at=? WHERE id=? AND {condition}",
                (phase, now, session_id, *params),
            )

    def append_event(self, session_id: str, event: str, data: dict,
                     size_bytes: int, run_id: str | None = None) -> int | None:
        now = utc_now()
        encoded = self._json(data)
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT event_seq,event_bytes,active_job_id,active_kind FROM sessions WHERE id=?",
                (session_id,)
            ).fetchone()
            if not row:
                return None
            sequence = row["event_seq"]
            effective_job_id = run_id or (row["active_job_id"] if row["active_kind"] == "chat" else None)
            projects_session = (run_id is None and row["active_kind"] == "chat") or (
                run_id is not None and row["active_job_id"] == run_id)
            connection.execute(
                "INSERT INTO events(session_id,seq,run_id,event,data_json,size_bytes,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (session_id, sequence, effective_job_id, event, encoded, size_bytes, now),
            )
            updates = ["event_seq=?", "event_bytes=event_bytes+?", "updated_at=?"]
            values: list = [sequence + 1, size_bytes, now]
            if projects_session and event == "state" and data.get("phase"):
                updates.append("phase=?")
                values.append(data["phase"])
            if event == "state" and data.get("phase") and run_id:
                connection.execute("UPDATE benchmark_runs SET phase=? WHERE id=? AND status='running'",
                                   (data["phase"], run_id))
            if projects_session and event == "artifact" and data.get("kind") == "spec":
                updates.append("spec_json=?")
                values.append(self._json(data.get("spec")))
            if projects_session and event == "artifact" and data.get("kind") == "results":
                updates.append("results_json=?")
                values.append(self._json(data.get("metrics")))
            if event == "artifact" and data.get("kind") == "results" and run_id:
                connection.execute("UPDATE benchmark_runs SET metrics_json=? WHERE id=? AND status='running'",
                                   (self._json(data.get("metrics")), run_id))
            connection.execute(f"UPDATE sessions SET {','.join(updates)} WHERE id=?",
                               (*values, session_id))
            if projects_session and event == "delta" and data.get("text"):
                latest = connection.execute(
                    "SELECT id,role,text FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                if latest and latest["role"] == "assistant":
                    connection.execute("UPDATE messages SET text=? WHERE id=?",
                                       ((latest["text"] + str(data["text"]))[-10000:], latest["id"]))
                else:
                    connection.execute(
                        "INSERT INTO messages(session_id,role,text,created_at) VALUES(?,?,?,?)",
                        (session_id, "assistant", str(data["text"])[:10000], now),
                    )
            count_cutoff = sequence - self.max_events
            if count_cutoff >= 0:
                removed = connection.execute(
                    "SELECT COALESCE(SUM(size_bytes),0) FROM events WHERE session_id=? AND seq<=?",
                    (session_id, count_cutoff),
                ).fetchone()[0]
                connection.execute("DELETE FROM events WHERE session_id=? AND seq<=?",
                                   (session_id, count_cutoff))
                if removed:
                    connection.execute("UPDATE sessions SET event_bytes=MAX(0,event_bytes-?) WHERE id=?",
                                       (removed, session_id))
            current_bytes = connection.execute(
                "SELECT event_bytes FROM sessions WHERE id=?", (session_id,)
            ).fetchone()[0]
            if current_bytes > self.max_event_bytes:
                excess = current_bytes - self.max_event_bytes
                rows = connection.execute(
                    "SELECT seq,size_bytes FROM events WHERE session_id=? ORDER BY seq", (session_id,)
                ).fetchall()
                removed = 0
                cutoff = None
                for item in rows:
                    removed += item["size_bytes"]
                    cutoff = item["seq"]
                    if removed >= excess:
                        break
                if cutoff is not None:
                    connection.execute("DELETE FROM events WHERE session_id=? AND seq<=?",
                                       (session_id, cutoff))
                    connection.execute("UPDATE sessions SET event_bytes=MAX(0,event_bytes-?) WHERE id=?",
                                       (removed, session_id))
            connection.execute(
                "DELETE FROM messages WHERE session_id=? AND id NOT IN "
                "(SELECT id FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                (session_id, session_id, self.max_messages),
            )
            return sequence

    def events_since(self, session_id: str, owner: str, cursor: int) -> list[tuple[int, str, dict]] | None:
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM sessions WHERE id=? AND owner=?",
                                      (session_id, owner)).fetchone():
                return None
            rows = connection.execute(
                "SELECT seq,event,data_json FROM events WHERE session_id=? AND seq>=? ORDER BY seq",
                (session_id, cursor),
            ).fetchall()
        return [(row["seq"], row["event"], self._decode(row["data_json"], {})) for row in rows]

    def event_records_since(self, session_id: str, owner: str, cursor: int) -> list[tuple[int, str, dict, str | None]] | None:
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM sessions WHERE id=? AND owner=?",
                                      (session_id, owner)).fetchone():
                return None
            rows = connection.execute(
                "SELECT seq,event,data_json,run_id FROM events WHERE session_id=? AND seq>=? ORDER BY seq",
                (session_id, cursor),
            ).fetchall()
        return [(row["seq"], row["event"], self._decode(row["data_json"], {}), row["run_id"])
                for row in rows]

    def stream_state(self, session_id: str, owner: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT event_seq,is_running,active_job_id,latest_job_id FROM sessions "
                "WHERE id=? AND owner=?", (session_id, owner),
            ).fetchone()
        return dict(row) if row else None

    def update_run_artifacts(self, run_id: str, *, spec=None, metrics=None,
                             report_md=None, citations=None, provenance=None) -> None:
        if provenance is not None and provenance not in {"measured", "synthetic"}:
            raise ValueError("invalid run provenance")
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE benchmark_runs SET spec_json=COALESCE(?,spec_json),"
                "metrics_json=COALESCE(?,metrics_json),report_md=COALESCE(?,report_md),"
                "citations_json=COALESCE(?,citations_json),provenance=COALESCE(?,provenance) "
                "WHERE id=? AND status='running'",
                (self._json(spec), self._json(metrics), report_md, self._json(citations),
                 provenance, run_id),
            )
            if result.rowcount != 1:
                raise KeyError(run_id)

    def get_run(self, run_id: str, owner: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_runs WHERE id=? AND owner=?", (run_id, owner)
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["spec"] = self._decode(value.pop("spec_json"))
        value["metrics"] = self._decode(value.pop("metrics_json"))
        value["citations"] = self._decode(value.pop("citations_json"), [])
        return value

    def delete_session(self, session_id: str, owner: str) -> tuple[bool, list[str]]:
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT is_running FROM sessions WHERE id=? AND owner=?",
                                     (session_id, owner)).fetchone()
            if not row:
                return False, []
            if row["is_running"]:
                raise BusyError("session is running")
            run_ids = [item[0] for item in connection.execute(
                "SELECT id FROM benchmark_runs WHERE session_id=?", (session_id,)).fetchall()]
            root = self.artifact_root
            self.enqueue_deletion("session", session_id,
                                  os.path.join(root, "sessions", session_id), connection)
            for run_id in run_ids:
                self.enqueue_deletion("run", run_id, os.path.join(root, run_id), connection)
            connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            return True, run_ids

    def recover_stale_jobs(self, *, force: bool = False) -> int:
        if not force and not self.acquire_leader("job-recovery", self.lease_seconds):
            return 0
        now = utc_now()
        orphan_before = (datetime.now(timezone.utc) - timedelta(
            seconds=self.orphan_grace_seconds)).isoformat().replace("+00:00", "Z")
        with self.transaction(immediate=True) as connection:
            interrupted = [dict(row) for row in connection.execute(
                "SELECT id,active_job_id,active_kind FROM sessions WHERE is_running=1 AND "
                "(? OR (active_lease_expires_at IS NOT NULL AND active_lease_expires_at<=?) OR "
                "(active_lease_expires_at IS NULL AND updated_at<=?))",
                (1 if force else 0, now, orphan_before),
            ).fetchall()]
            job_ids = [row["active_job_id"] for row in interrupted if row["active_job_id"]]
            if not interrupted:
                return 0
            placeholders = ",".join("?" for _ in interrupted)
            session_ids = [row["id"] for row in interrupted]
            run_count = connection.execute(
                "UPDATE benchmark_runs SET status='failed',phase='FAILED',finished_at=? "
                f"WHERE status='running' AND session_id IN ({placeholders})", (now, *session_ids)
            ).rowcount
            connection.execute(
                "UPDATE sessions SET is_running=0,active_kind=NULL,active_job_id=NULL,"
                "active_worker_id=NULL,active_lease_expires_at=NULL,cancel_requested=0,"
                f"phase='FAILED',updated_at=? WHERE id IN ({placeholders})", (now, *session_ids)
            )
            if job_ids:
                job_placeholders = ",".join("?" for _ in job_ids)
                connection.execute(
                    f"UPDATE chat_jobs SET status='failed',finished_at=? WHERE id IN ({job_placeholders})",
                    (now, *job_ids),
                )
        for session in interrupted:
            job_id = session["active_job_id"]
            for event, data in (
                ("error", {"message": "The worker stopped responding before this operation completed."}),
                ("state", {"phase": "FAILED", "candidates": {}}),
                ("done", {}),
            ):
                encoded_size = len(json.dumps((event, data)).encode("utf-8"))
                self.append_event(session["id"], event, data, encoded_size, job_id)
        return run_count

    def mark_interrupted(self) -> int:
        """Compatibility hook for explicit administrative recovery."""
        return self.recover_stale_jobs(force=True)

    def enqueue_deletion(self, resource_kind: str, resource_id: str, path: str,
                         connection: sqlite3.Connection | None = None) -> None:
        now = utc_now()
        target = connection or self.connect()
        try:
            target.execute(
                "INSERT INTO deletion_queue(resource_kind,resource_id,path,status,attempts,"
                "next_attempt_at,created_at,updated_at) VALUES(?,?,?,'pending',0,?,?,?) "
                "ON CONFLICT(resource_kind,resource_id,path) DO UPDATE SET status='pending',"
                "next_attempt_at=excluded.next_attempt_at,updated_at=excluded.updated_at",
                (resource_kind, resource_id, os.path.realpath(path), now, now, now),
            )
        finally:
            if connection is None:
                target.close()

    def due_deletions(self, limit: int = 100) -> list[dict]:
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            # A worker may exit after claiming work. The next-attempt timestamp doubles as
            # a bounded processing lease so another process can safely recover the item.
            connection.execute(
                "UPDATE deletion_queue SET status='pending',updated_at=? "
                "WHERE status='processing' AND next_attempt_at<=?", (now, now))
            rows = connection.execute(
                "SELECT * FROM deletion_queue WHERE status='pending' AND next_attempt_at<=? "
                "ORDER BY id LIMIT ?", (now, max(1, min(limit, 1000))),
            ).fetchall()
            if rows:
                placeholders = ",".join("?" for _ in rows)
                connection.execute(
                    f"UPDATE deletion_queue SET status='processing',next_attempt_at=?,updated_at=? "
                    f"WHERE id IN ({placeholders}) AND status='pending'",
                    (utc_after(self.lease_seconds), now, *(row["id"] for row in rows)),
                )
        return [dict(row) for row in rows]

    def finish_deletion(self, queue_id: int, success: bool, error_type: str = "") -> None:
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM deletion_queue WHERE id=?", (queue_id,)).fetchone()
            if not row:
                return
            if success:
                connection.execute("UPDATE deletion_queue SET status='done',updated_at=? WHERE id=?",
                                   (now, queue_id))
                if row["resource_kind"] == "dataset":
                    connection.execute("DELETE FROM datasets WHERE id=? AND status='deleting'",
                                       (row["resource_id"],))
            else:
                attempts = row["attempts"] + 1
                delay = min(3600, 2 ** min(attempts, 10))
                connection.execute(
                    "UPDATE deletion_queue SET status='pending',attempts=?,next_attempt_at=?,"
                    "last_error=?,updated_at=? "
                    "WHERE id=?", (attempts, utc_after(delay), error_type[:120], now, queue_id),
                )

    def deletion_summary(self) -> dict:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status,COUNT(*) AS count,COALESCE(SUM(attempts),0) AS attempts "
                "FROM deletion_queue GROUP BY status"
            ).fetchall()
        return {row["status"]: {"count": row["count"], "attempts": row["attempts"]}
                for row in rows}

    def operational_summary(self) -> dict:
        with self.connect() as connection:
            active_runs = connection.execute(
                "SELECT COUNT(*) FROM benchmark_runs WHERE status='running'"
            ).fetchone()[0]
            failed_runs = connection.execute(
                "SELECT COUNT(*) FROM benchmark_runs WHERE status='failed'"
            ).fetchone()[0]
            active_chats = connection.execute(
                "SELECT COUNT(*) FROM chat_jobs WHERE status='running'"
            ).fetchone()[0]
            datasets = connection.execute(
                "SELECT COUNT(*) FROM datasets WHERE status='active'"
            ).fetchone()[0]
        return {"active_runs": active_runs, "failed_runs": failed_runs,
                "active_chats": active_chats, "datasets": datasets,
                "deletions": self.deletion_summary()}

    def cleanup_expired(self, retention_days: int) -> dict[str, list[str]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).isoformat().replace(
            "+00:00", "Z")
        with self.transaction(immediate=True) as connection:
            sessions = connection.execute(
                "SELECT id,dataset_id FROM sessions WHERE is_running=0 AND updated_at<?", (cutoff,)
            ).fetchall()
            session_ids = [row["id"] for row in sessions]
            run_ids: list[str] = []
            if session_ids:
                placeholders = ",".join("?" for _ in session_ids)
                run_ids = [row[0] for row in connection.execute(
                    f"SELECT id FROM benchmark_runs WHERE session_id IN ({placeholders})", session_ids
                ).fetchall()]
                root = self.artifact_root
                for session_id in session_ids:
                    self.enqueue_deletion("session", session_id,
                                          os.path.join(root, "sessions", session_id), connection)
                for run_id in run_ids:
                    self.enqueue_deletion("run", run_id, os.path.join(root, run_id), connection)
                connection.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", session_ids)
            referenced = [row[0] for row in connection.execute(
                "SELECT dataset_id FROM sessions WHERE dataset_id IS NOT NULL UNION "
                "SELECT dataset_id FROM benchmark_runs WHERE dataset_id IS NOT NULL").fetchall()]
            expired_rows = connection.execute(
                "SELECT id,path FROM datasets WHERE status='active' AND kind!='synthetic' AND created_at<? "
                "AND id NOT IN (SELECT dataset_id FROM sessions WHERE dataset_id IS NOT NULL) "
                "AND id NOT IN (SELECT dataset_id FROM benchmark_runs WHERE dataset_id IS NOT NULL)",
                (cutoff,),
            ).fetchall()
            expired_datasets = [row["id"] for row in expired_rows]
            for row in expired_rows:
                connection.execute("UPDATE datasets SET status='deleting' WHERE id=?", (row["id"],))
                self.enqueue_deletion("dataset", row["id"], row["path"], connection)
        return {"session_ids": session_ids, "run_ids": run_ids,
                "referenced_dataset_ids": referenced, "dataset_ids": expired_datasets}

    def consume_request(self, owner: str, limit: int, window_seconds: int = 60) -> bool:
        if limit <= 0:
            return True
        bucket = int(time.time()) // max(1, window_seconds)
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT count FROM request_usage WHERE owner=? AND bucket=?", (owner, bucket)
            ).fetchone()
            if row and row["count"] >= limit:
                return False
            connection.execute(
                "INSERT INTO request_usage(owner,bucket,count) VALUES(?,?,1) "
                "ON CONFLICT(owner,bucket) DO UPDATE SET count=count+1", (owner, bucket),
            )
            connection.execute("DELETE FROM request_usage WHERE bucket<?", (bucket - 2,))
            return True
