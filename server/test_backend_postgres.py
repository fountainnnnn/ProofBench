"""PostgreSQL contract tests; enabled with PROOFBENCH_TEST_POSTGRES_URL."""
from __future__ import annotations

import os
import threading
import uuid

import pytest

from server.postgres_state import PostgresStore
from server.state import BusyError


def _url() -> str:
    value = os.environ.get("PROOFBENCH_TEST_POSTGRES_URL", "").strip()
    if not value:
        pytest.skip("PROOFBENCH_TEST_POSTGRES_URL is not configured")
    return value


def _store(tmp_path, **kwargs) -> PostgresStore:
    return PostgresStore(
        _url(), recover_interrupted=False, artifact_root=str(tmp_path), **kwargs,
    )


def _spec() -> dict:
    return {
        "benchmark_type": "extraction",
        "category": "documents",
        "fields": ["invoice_number"],
        "candidates": [{"name": "ocr"}],
        "dataset": {"path": "server-owned"},
    }


def test_postgres_state_round_trip_and_schema(tmp_path):
    store = _store(tmp_path)
    owner = f"pg-{uuid.uuid4().hex}"
    with store.connect() as connection:
        assert connection.execute(
            "SELECT version FROM proofbench_schema WHERE singleton=TRUE"
        ).fetchone()[0] >= 5

    session = store.create_session(owner, "Railway trial")
    store.add_message(session["id"], "user", "Compare OCR tools")
    store.add_findings(session["id"], [
        {"title": "Docs", "url": "https://example.com/docs"},
        {"title": "Duplicate", "url": "https://example.com/docs"},
    ])
    store.append_event(session["id"], "delta", {"text": "working"}, 32)
    store.set_setting(owner, "theme", "light")
    store.set_credential(owner, "OPENROUTER_API_KEY", "trial-secret")

    restored = store.get_session(session["id"], owner)
    assert restored["messages"][-1]["text"] == "Compare OCR tools"
    assert restored["events"][-1][2] == {"text": "working"}
    assert store.list_findings(session["id"]) == [
        {"title": "Docs", "url": "https://example.com/docs"}
    ]
    assert store.get_setting(owner, "theme") == "light"
    assert store.credentials(owner)["OPENROUTER_API_KEY"] == "trial-secret"
    assert store.consume_request(owner, 2)
    assert store.consume_request(owner, 2)
    assert not store.consume_request(owner, 2)

    dataset = store.reserve_dataset(
        owner, lambda dataset_id: f"/owned/{dataset_id}", 128, 1024,
    )
    activated = store.activate_dataset(
        dataset["id"], owner, image_count=1, total_bytes=128,
    )
    assert activated["status"] == "active"

    run = store.claim_benchmark(session["id"], owner, _spec(), "real", 2, 100)
    store.update_run_artifacts(run["id"], metrics={"winner": "ocr"}, provenance="measured")
    store.finish(session["id"], run_id=run["id"])
    assert store.get_run(run["id"], owner)["provenance"] == "measured"


def test_postgres_claim_is_atomic_across_connections(tmp_path):
    first = _store(tmp_path, worker_id=f"a-{uuid.uuid4().hex}")
    second = _store(tmp_path, worker_id=f"b-{uuid.uuid4().hex}")
    owner = f"pg-{uuid.uuid4().hex}"
    session = first.create_session(owner, "Atomic Railway claim")
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def claim(store):
        barrier.wait()
        try:
            outcomes.append(store.claim_benchmark(
                session["id"], owner, _spec(), "real", 2, 100,
            )["id"])
        except BusyError:
            outcomes.append("busy")

    threads = [threading.Thread(target=claim, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("busy") == 1
    assert len([item for item in outcomes if item != "busy"]) == 1
