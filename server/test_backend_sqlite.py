"""Multi-process-style durability and immutable-run regression tests."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from server.state import SCHEMA_VERSION, BusyError, QuotaError, SQLiteStore


def extraction_spec() -> dict:
    return {"benchmark_type": "extraction", "category": "documents",
            "fields": ["invoice_number"], "candidates": [{"name": "ocr"}],
            "dataset": {"path": "server-owned"}}


def stores(tmp_path, **kwargs):
    path = str(tmp_path / "proofbench.sqlite3")
    return (SQLiteStore(path, recover_interrupted=False, **kwargs),
            SQLiteStore(path, recover_interrupted=False, **kwargs))


def test_two_store_instances_share_sessions_and_events(tmp_path):
    first, second = stores(tmp_path)
    session = first.create_session("tenant-a", "Shared")
    first.append_event(session["id"], "delta", {"text": "visible"}, 32)
    assert second.get_session(session["id"], "tenant-a")["title"] == "Shared"
    assert second.events_since(session["id"], "tenant-a", 0) == [
        (0, "delta", {"text": "visible"})]
    assert second.events_since(session["id"], "tenant-b", 0) is None


def test_session_summaries_expose_last_activity_time(tmp_path):
    first, _ = stores(tmp_path)
    session = first.create_session("tenant-a", "Activity")
    touched = "2026-07-28T06:02:05.977175Z"
    with first.connect() as connection:
        connection.execute(
            "UPDATE sessions SET updated_at=? WHERE id=?",
            (touched, session["id"]),
        )

    [summary] = first.list_sessions("tenant-a")

    assert summary["updated_at"] == touched


def test_reruns_are_distinct_and_history_is_durable(tmp_path):
    first, second = stores(tmp_path)
    session = first.create_session("tenant-a", "Rerun")
    one = first.claim_benchmark(session["id"], "tenant-a", extraction_spec(), "real", 2, 100)
    first.finish(session["id"], run_id=one["id"])
    two = second.claim_benchmark(session["id"], "tenant-a", extraction_spec(), "real", 2, 100)
    second.finish(session["id"], run_id=two["id"])
    restored = first.get_session(session["id"], "tenant-a")
    assert one["id"] != two["id"]
    assert restored["latest_run_id"] == two["id"]
    assert [item["id"] for item in restored["run_history"]] == [two["id"], one["id"]]


def test_atomic_claim_across_independent_stores(tmp_path):
    first, second = stores(tmp_path)
    session = first.create_session("tenant-a", "Atomic")
    barrier = threading.Barrier(2)
    outcomes = []

    def claim(store):
        barrier.wait()
        try:
            outcomes.append(store.claim_benchmark(
                session["id"], "tenant-a", extraction_spec(), "real", 2, 100)["id"])
        except BusyError:
            outcomes.append("busy")

    threads = [threading.Thread(target=claim, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("busy") == 1
    assert len([value for value in outcomes if value != "busy"]) == 1


def test_tenant_concurrency_and_daily_quotas_are_atomic(tmp_path):
    first, _ = stores(tmp_path)
    session_a = first.create_session("tenant-a", "One")
    session_b = first.create_session("tenant-a", "Two")
    run = first.claim_benchmark(session_a["id"], "tenant-a", extraction_spec(), "real", 1, 100)
    with pytest.raises(QuotaError):
        first.claim_benchmark(session_b["id"], "tenant-a", extraction_spec(), "real", 1, 100)
    first.finish(session_a["id"], run_id=run["id"])
    with pytest.raises(QuotaError):
        first.claim_benchmark(session_b["id"], "tenant-a", extraction_spec(), "real", 2, 1)


def test_event_retention_is_bounded_without_session_blob_rewrites(tmp_path):
    first, second = stores(tmp_path, max_events=5, max_event_bytes=4096)
    session = first.create_session("tenant-a", "Events")
    for index in range(12):
        first.append_event(session["id"], "delta", {"text": str(index)}, 32)
    events = second.events_since(session["id"], "tenant-a", 0)
    assert len(events) == 5
    assert [item[0] for item in events] == [7, 8, 9, 10, 11]


def test_restart_preserves_live_lease_and_recovers_expired_job(tmp_path):
    first, _ = stores(tmp_path, worker_id="worker-a")
    session = first.create_session("tenant-a", "Interrupted")
    run = first.claim_benchmark(session["id"], "tenant-a", extraction_spec(), "real", 2, 100)
    restarted = SQLiteStore(first.path, recover_interrupted=True, worker_id="worker-b")
    assert restarted.get_session(session["id"], "tenant-a")["is_running"] is True
    assert restarted.get_run(run["id"], "tenant-a")["status"] == "running"
    with first.connect() as connection:
        connection.execute(
            "UPDATE sessions SET active_lease_expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00Z", session["id"]),
        )
        connection.execute(
            "UPDATE benchmark_runs SET lease_expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00Z", run["id"]),
        )
    restarted.recover_stale_jobs()
    assert restarted.get_session(session["id"], "tenant-a")["phase"] == "FAILED"
    assert restarted.get_run(run["id"], "tenant-a")["status"] == "failed"
    first.finish(session["id"], run_id=run["id"])
    assert restarted.get_run(run["id"], "tenant-a")["status"] == "failed"


def test_retention_never_deletes_active_jobs(tmp_path):
    first, _ = stores(tmp_path)
    idle = first.create_session("tenant-a", "Old idle")
    active = first.create_session("tenant-a", "Old active")
    run = first.claim_benchmark(active["id"], "tenant-a", extraction_spec(), "real", 2, 100)
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
    with first.connect() as connection:
        connection.execute("UPDATE sessions SET updated_at=?", (old,))
    removed = first.cleanup_expired(30)
    assert idle["id"] in removed["session_ids"]
    assert active["id"] not in removed["session_ids"]
    assert first.get_run(run["id"], "tenant-a") is not None


def test_store_owner_checks_runs_and_sessions(tmp_path):
    first, _ = stores(tmp_path)
    session = first.create_session("tenant-a", "Private")
    run = first.claim_benchmark(session["id"], "tenant-a", extraction_spec(), "real", 2, 100)
    assert first.get_session(session["id"], "tenant-b") is None
    assert first.get_run(run["id"], "tenant-b") is None


def test_request_quota_is_shared_between_store_instances(tmp_path):
    first, second = stores(tmp_path)
    assert first.consume_request("tenant-a", 2)
    assert second.consume_request("tenant-a", 2)
    assert not first.consume_request("tenant-a", 2)
    assert second.consume_request("tenant-b", 2)


def test_leader_lease_is_exclusive_and_transfers_after_expiry(tmp_path):
    first, second = stores(tmp_path, worker_id="worker-a")
    second.worker_id = "worker-b"
    second.register_worker()
    assert first.acquire_leader("retention")
    assert not second.acquire_leader("retention")
    with first.connect() as connection:
        connection.execute("UPDATE leader_leases SET expires_at=? WHERE name=?",
                           ("2000-01-01T00:00:00Z", "retention"))
    assert second.acquire_leader("retention")


def test_stale_run_events_are_retained_without_projecting_session_state(tmp_path):
    first, second = stores(tmp_path)
    session = first.create_session("tenant-a", "Stale events")
    old_run = first.claim_benchmark(session["id"], "tenant-a", extraction_spec(), "real", 2, 100)
    first.append_event(session["id"], "state", {"phase": "OLD_ACTIVE"}, 32, old_run["id"])
    first.finish(session["id"], run_id=old_run["id"])
    current_run = second.claim_benchmark(
        session["id"], "tenant-a", extraction_spec(), "real", 2, 100)
    second.append_event(session["id"], "state", {"phase": "CURRENT_ACTIVE"}, 32,
                        current_run["id"])
    second.append_event(session["id"], "artifact",
                        {"kind": "results", "metrics": {"winner": "current"}}, 64,
                        current_run["id"])
    first.append_event(session["id"], "state", {"phase": "STALE_FAILED"}, 32, old_run["id"])
    first.append_event(session["id"], "artifact",
                       {"kind": "results", "metrics": {"winner": "stale"}}, 64,
                       old_run["id"])
    restored = first.get_session(session["id"], "tenant-a")
    assert restored["phase"] == "CURRENT_ACTIVE"
    assert restored["results"] == {"winner": "current"}
    records = second.event_records_since(session["id"], "tenant-a", 0)
    assert any(event == "state" and data.get("phase") == "STALE_FAILED" and job == old_run["id"]
               for _, event, data, job in records)


def test_dataset_registry_and_quota_are_cross_process_authoritative(tmp_path):
    first, second = stores(tmp_path)
    root = tmp_path / "datasets"
    path_for = lambda dataset_id: str(root / dataset_id)
    reservation = first.reserve_dataset("tenant-a", path_for, 60, 100)
    first.activate_dataset(reservation["id"], "tenant-a", image_count=1, total_bytes=60)
    assert second.get_dataset(reservation["id"], "tenant-a")["total_bytes"] == 60
    with pytest.raises(QuotaError):
        second.reserve_dataset("tenant-a", path_for, 41, 100)
    session = second.create_session("tenant-a", "Dataset reference")
    run = second.claim_benchmark(session["id"], "tenant-a", extraction_spec(), "real", 2, 100,
                                 reservation["id"])
    second.finish(session["id"], run_id=run["id"])
    with pytest.raises(BusyError):
        first.begin_dataset_delete(reservation["id"], "tenant-a")


def test_concurrent_dataset_reservations_do_not_overcommit_quota(tmp_path):
    first, second = stores(tmp_path)
    barrier = threading.Barrier(2)
    outcomes = []

    def reserve(store):
        barrier.wait()
        try:
            outcomes.append(store.reserve_dataset(
                "tenant-a", lambda value: str(tmp_path / "uploads" / value), 70, 100)["id"])
        except QuotaError:
            outcomes.append("quota")

    threads = [threading.Thread(target=reserve, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("quota") == 1
    assert len([value for value in outcomes if value != "quota"]) == 1


def test_deletion_queue_claim_is_cross_process_exclusive_and_recoverable(tmp_path):
    first, second = stores(tmp_path)
    first.enqueue_deletion("run", "abc123abc123", str(tmp_path / "abc123abc123"))
    claimed = first.due_deletions()
    assert len(claimed) == 1
    assert second.due_deletions() == []
    with first.connect() as connection:
        connection.execute("UPDATE deletion_queue SET next_attempt_at=? WHERE id=?",
                           ("2000-01-01T00:00:00Z", claimed[0]["id"]))
    reclaimed = second.due_deletions()
    assert [item["id"] for item in reclaimed] == [claimed[0]["id"]]


def test_chat_claim_quota_and_mutation_are_atomic_across_stores(tmp_path):
    first, second = stores(tmp_path)
    dataset = first.reserve_dataset("tenant-a", lambda value: str(tmp_path / value), 1, 100)
    first.activate_dataset(dataset["id"], "tenant-a", image_count=1, total_bytes=1)
    one = first.create_session("tenant-a", "One")
    two = first.create_session("tenant-a", "Two")
    claimed = first.claim_chat_atomic(one["id"], "tenant-a", dataset["id"], "real", "one", 1, 10)
    before = second.get_session(two["id"], "tenant-a")
    with pytest.raises(QuotaError):
        second.claim_chat_atomic(two["id"], "tenant-a", dataset["id"], "demo", "rejected", 1, 10)
    after = first.get_session(two["id"], "tenant-a")
    assert after["mode"] == before["mode"]
    assert after["dataset_id"] == before["dataset_id"]
    assert after["messages"] == before["messages"]
    first.finish(one["id"])
    second.claim_chat_atomic(two["id"], "tenant-a", dataset["id"], "demo", "accepted", 2, 2)
    first.finish(two["id"])
    three = first.create_session("tenant-a", "Three")
    with pytest.raises(QuotaError):
        first.claim_chat_atomic(three["id"], "tenant-a", dataset["id"], "real", "daily", 2, 2)


def test_recovered_chat_terminal_events_keep_their_job_identity(tmp_path):
    first, second = stores(tmp_path)
    dataset = first.reserve_dataset("tenant-a", lambda value: str(tmp_path / value), 1, 100)
    first.activate_dataset(dataset["id"], "tenant-a", image_count=1, total_bytes=1)
    session = first.create_session("tenant-a", "Recover chat")
    job = first.claim_chat_atomic(
        session["id"], "tenant-a", dataset["id"], "demo", "hello", 2, 10)
    with first.connect() as connection:
        connection.execute("UPDATE sessions SET active_lease_expires_at=? WHERE id=?",
                           ("2000-01-01T00:00:00Z", session["id"]))
    second.recover_stale_jobs(force=True)
    records = second.event_records_since(session["id"], "tenant-a", 0)
    assert [(event, event_job) for _, event, _, event_job in records][-3:] == [
        ("error", job["id"]), ("state", job["id"]), ("done", job["id"])]
    current = second.claim_chat_atomic(
        session["id"], "tenant-a", dataset["id"], "real", "again", 2, 10)
    second.append_event(session["id"], "state", {"phase": "CURRENT_CHAT"}, 32, current["id"])
    first.finish(session["id"], job_id=job["id"])
    restored = second.get_session(session["id"], "tenant-a")
    assert restored["is_running"] is True
    assert restored["phase"] == "CURRENT_CHAT"
    with second.connect() as connection:
        assert connection.execute("SELECT status FROM chat_jobs WHERE id=?", (job["id"],)).fetchone()[0] == "failed"


def test_v1_migration_is_atomic_backed_up_and_rejects_future_schema(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                phase TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                spec_json TEXT, results_json TEXT, mode TEXT NOT NULL DEFAULT 'demo',
                dataset_id TEXT, dataset_path TEXT, is_running INTEGER NOT NULL DEFAULT 0,
                active_kind TEXT, active_job_id TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                event_seq INTEGER NOT NULL DEFAULT 0, event_bytes INTEGER NOT NULL DEFAULT 0,
                latest_run_id TEXT
            );
            CREATE TABLE datasets (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
            );
            CREATE TABLE benchmark_runs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                owner TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL, mode TEXT NOT NULL,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, spec_json TEXT,
                metrics_json TEXT, report_md TEXT, citations_json TEXT
            );
            PRAGMA user_version=1;
        """)
    monkeypatch.setenv("PROOFBENCH_MIGRATION_BACKUP", "1")
    barrier = threading.Barrier(2)
    errors = []

    def migrate(worker):
        barrier.wait()
        try:
            SQLiteStore(str(path), worker_id=worker)
        except Exception as exc:  # surfaced below with the exact exception
            errors.append(exc)

    threads = [threading.Thread(target=migrate, args=(worker,))
               for worker in ("worker-a", "worker-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with sqlite3.connect(path) as connection:
        # Bound to the constant: this test is about the migration being atomic
        # under concurrency, not about which version happens to be current.
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "provenance" in {
            row[1] for row in connection.execute("PRAGMA table_info(benchmark_runs)")}
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "active_lease_expires_at" in {
            row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='deletion_queue'").fetchone()
    assert list(tmp_path.glob("legacy.sqlite3.pre-v1-*.bak"))
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="unsupported"):
        SQLiteStore(str(path), worker_id="future-reader")
