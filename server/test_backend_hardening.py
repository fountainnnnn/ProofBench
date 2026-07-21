"""Security and durability regression tests for the ProofBench API layer."""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from PIL import Image

from server import runs
from server.main import app
from fastapi import HTTPException

from server.security import (authenticate, auth_is_configured, authenticate_token,
                             check_auth_mode, provider_credentials)
from server.security import Identity
from server.storage import DatasetRegistry
from server.state import SQLiteStore
import server.main as main_module
import server.storage as storage_module


TOKEN_A = "tenant-a-production-test-token-0001"
TOKEN_B = "tenant-b-production-test-token-0002"


def _isolate_storage(tmp_path, monkeypatch) -> None:
    """Point runs, uploads, and durable state at a per-test temporary tree."""
    monkeypatch.setenv("PROOFBENCH_RECONCILE_SANDBOXES_ON_STARTUP", "0")
    run_dir = tmp_path / "runs"
    upload_dir = tmp_path / "data" / "uploads"
    demo_dir = tmp_path / "data" / "demo"
    (demo_dir / "images").mkdir(parents=True)
    monkeypatch.setattr(runs, "RUNS_DIR", str(run_dir))
    monkeypatch.setattr(runs, "STORE", SQLiteStore(str(tmp_path / "state.sqlite3"),
                                                   recover_interrupted=False,
                                                   artifact_root=str(run_dir)))
    monkeypatch.setattr(storage_module, "UPLOADS_DIR", str(upload_dir))
    monkeypatch.setattr(main_module, "UPLOADS_DIR", str(upload_dir))
    registry = DatasetRegistry()
    monkeypatch.setattr(main_module, "datasets", registry)
    for tenant in ("tenant-a", "tenant-b", "local-dev"):
        for name in provider_credentials.names(tenant):
            provider_credentials.delete(tenant, name)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The authenticated profile: tokens configured, no local bypass."""
    monkeypatch.setenv("PROOFBENCH_API_KEYS", json.dumps({"tenant-a": TOKEN_A, "tenant-b": TOKEN_B}))
    monkeypatch.delenv("PROOFBENCH_INSECURE_DEV", raising=False)
    _isolate_storage(tmp_path, monkeypatch)
    with TestClient(app) as value:
        yield value


@pytest.fixture()
def local_client(tmp_path, monkeypatch):
    """The default local profile: PROOFBENCH_INSECURE_DEV=1 and no tokens."""
    monkeypatch.delenv("PROOFBENCH_API_KEYS", raising=False)
    monkeypatch.setenv("PROOFBENCH_INSECURE_DEV", "1")
    _isolate_storage(tmp_path, monkeypatch)
    with TestClient(app) as value:
        yield value


def headers(token: str) -> dict[str, str]:
    resolved = {"token-a": TOKEN_A, "token-b": TOKEN_B}.get(token, token)
    return {"Authorization": f"Bearer {resolved}"}


def create_session(client: TestClient, token: str = "token-a") -> str:
    response = client.post("/api/sessions", headers=headers(token))
    assert response.status_code == 200
    return response.json()["session_id"]


def valid_spec(dataset_path: str | None = None) -> dict:
    dataset = {"path": dataset_path} if dataset_path is not None else None
    return {
        "category": "Document intelligence",
        "fields": ["invoice_number", "date", "vendor", "total"],
        "candidates": [{"name": "tesseract", "docs_url": "", "pricing_url": "",
                        "kind": "local_tool"}],
        "dataset": dataset,
    }


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, "PNG")
    return output.getvalue()


def ground_truth(doc_id: str = "inv_001") -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["doc_id", "invoice_number", "date", "vendor", "total"])
    writer.writerow([doc_id, "INV-1", "2026-07-20", "Acme", "1.00"])
    return output.getvalue().encode()


def upload_dataset(client: TestClient, token: str = "token-a") -> dict:
    response = client.post("/api/datasets", headers=headers(token), files=[
        ("images", ("inv_001.png", png_bytes(), "image/png")),
        ("ground_truth", ("ground_truth.csv", ground_truth(), "text/csv")),
    ])
    assert response.status_code == 200
    return response.json()


def test_authentication_is_fail_closed_and_live_is_public(client):
    live = client.get("/api/live", headers={"X-Request-ID": "test-request-123"})
    assert live.status_code == 200
    assert live.headers["x-request-id"] == "test-request-123"
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers=headers("wrong")).status_code == 401


def test_local_mode_reports_itself_and_serves_reads_without_a_token(local_client):
    status = local_client.get("/api/auth/session")
    assert status.status_code == 200
    assert status.json() == {"auth_mode": "local", "cookie_authenticated": True,
                             "write_authenticated": True}
    # No Authorization header at all — not an empty or placeholder one.
    assert local_client.get("/api/sessions").status_code == 200


def test_local_mode_accepts_a_tokenless_write_and_scopes_it_to_the_local_tenant(local_client):
    created = local_client.post("/api/sessions")
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    listed = local_client.get("/api/sessions")
    assert listed.status_code == 200
    assert session_id in {session["id"] for session in listed.json()}

    # Every tokenless caller resolves to the same deterministic local tenant, so
    # the write is readable back rather than stranded in a per-request identity.
    assert local_client.get(f"/api/sessions/{session_id}").status_code == 200
    with runs.STORE.connect() as connection:
        owner = connection.execute(
            "SELECT owner FROM sessions WHERE id=?", (session_id,)
        ).fetchone()[0]
    assert owner == "local-dev"


def test_local_mode_still_refuses_runtime_provider_credential_writes(local_client, monkeypatch):
    # The tokenless bypass covers authentication only. Writing provider secrets
    # at runtime stays off unless it is separately and explicitly enabled.
    monkeypatch.delenv("PROOFBENCH_ALLOW_RUNTIME_CREDENTIALS", raising=False)
    response = local_client.post("/api/settings/provider-keys",
                                 json={"env": "CUSTOM_API_KEY", "value": "secret-value"})
    assert response.status_code == 503
    assert "secret-value" not in response.text
    settings = local_client.get("/api/settings/provider-keys").json()
    assert settings["runtime_writes_enabled"] is False
    assert all(item["env"] != "CUSTOM_API_KEY" for item in settings["keys"])


def test_authenticated_mode_rejects_the_missing_token_local_mode_would_accept(client):
    # The exact requests the local profile serves must still fail closed here.
    assert client.get("/api/sessions").status_code == 401
    assert client.post("/api/sessions").status_code == 401
    status = client.get("/api/auth/session")
    assert status.status_code == 200
    assert status.json() == {"auth_mode": "authenticated", "cookie_authenticated": False,
                             "write_authenticated": False}


def test_container_readiness_probe_supports_local_and_authenticated_profiles():
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile.api").read_text(
        encoding="utf-8"
    )
    assert "PROOFBENCH_INSECURE_DEV" in dockerfile
    assert "headers={} if local else" in dockerfile
    assert "os.environ.get('PROOFBENCH_API_KEYS') or '{}'" in dockerfile


def test_reverse_proxy_replaces_untrusted_forwarded_for_before_login_throttling():
    nginx = (Path(__file__).resolve().parent.parent / "web" / "nginx.conf").read_text(
        encoding="utf-8"
    )
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx


def test_sensitive_api_responses_are_no_store(client):
    response = client.get("/api/sessions", headers=headers("token-a"))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/api/live").headers


@pytest.mark.parametrize("token", ["short", "replace-with-at-least-32-random-characters",
                                    "0123456789abcdef0123456789abcdef"])
def test_production_rejects_weak_or_shipped_example_tokens(monkeypatch, token):
    monkeypatch.setenv("PROOFBENCH_API_KEYS", json.dumps({"tenant": token}))
    monkeypatch.delenv("PROOFBENCH_INSECURE_DEV", raising=False)
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        auth_is_configured()


def test_every_non_liveness_route_has_auth_dependency(client):
    bootstrap = {"/api/auth/session"}
    for route in app.routes:
        if (isinstance(route, APIRoute) and route.path != "/api/live" and
                route.path not in bootstrap):
            assert authenticate in {dependency.call for dependency in route.dependant.dependencies}, route.path


def test_eventsource_cookie_bootstrap_and_logout(client):
    assert client.get("/api/auth/session").json() == {
        "auth_mode": "authenticated", "cookie_authenticated": False,
        "write_authenticated": False}
    response = client.post("/api/auth/session", headers=headers("token-a"))
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "proofbench_api_key=" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    assert "token-a" not in response.text
    assert client.get("/api/auth/session").json() == {
        "auth_mode": "authenticated", "cookie_authenticated": True,
        "write_authenticated": False}
    assert client.get("/api/sessions").status_code == 200
    assert client.post("/api/sessions").status_code == 403
    assert client.delete("/api/auth/session").status_code == 403
    assert client.get("/api/sessions").status_code == 200
    assert client.delete("/api/auth/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.delete("/api/auth/session", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.get("/api/sessions").status_code == 401


def test_cookie_bootstrap_accepts_body_token(client):
    response = client.post("/api/auth/session", json={"token": TOKEN_B})
    assert response.status_code == 200
    assert client.get("/api/sessions").status_code == 200


def test_request_quota_returns_429(client, monkeypatch):
    monkeypatch.setattr(runs, "REQUESTS_PER_MINUTE", 1)
    assert client.get("/api/sessions", headers=headers("token-a")).status_code == 200
    response = client.get("/api/sessions", headers=headers("token-a"))
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_cors_origins_are_explicit(monkeypatch):
    monkeypatch.setenv("PROOFBENCH_ALLOWED_ORIGINS", "https://app.example.com,http://localhost:5173/")
    assert main_module._allowed_origins() == ["https://app.example.com", "http://localhost:5173"]
    monkeypatch.setenv("PROOFBENCH_ALLOWED_ORIGINS", "*")
    with pytest.raises(RuntimeError):
        main_module._allowed_origins()


def test_sessions_and_results_are_tenant_isolated(client):
    session_id = create_session(client)
    assert client.get(f"/api/sessions/{session_id}", headers=headers("token-a")).status_code == 200
    assert client.get(f"/api/sessions/{session_id}", headers=headers("token-b")).status_code == 404
    assert client.get(f"/api/runs/{session_id}/results", headers=headers("token-b")).status_code == 404
    assert client.get(f"/api/runs/{session_id}/results", headers=headers("token-a")).status_code == 404


def test_runtime_provider_credentials_are_disabled_in_production(client):
    response = client.post("/api/settings/provider-keys", headers=headers("token-a"),
                           json={"env": "CUSTOM_API_KEY", "value": "secret-value"})
    assert response.status_code == 503
    assert "secret-value" not in response.text
    settings = client.get("/api/settings/provider-keys", headers=headers("token-a")).json()
    assert settings["runtime_writes_enabled"] is False
    assert settings["managed_by"] == "deployment"
    assert all(item["env"] != "CUSTOM_API_KEY" for item in settings["keys"])


def test_upload_validation_and_dataset_ownership(client):
    files = [
        ("images", ("inv_001.png", png_bytes(), "image/png")),
        ("ground_truth", ("ground_truth.csv", ground_truth(), "text/csv")),
    ]
    response = client.post("/api/datasets", headers=headers("token-a"), files=files)
    assert response.status_code == 200
    dataset_id = response.json()["dataset_id"]
    session_b = create_session(client, "token-b")
    chat = client.post("/api/chat", headers=headers("token-b"), json={
        "session_id": session_b, "message": "benchmark", "dataset_id": dataset_id})
    assert chat.status_code == 404

    bad = client.post("/api/datasets", headers=headers("token-a"), files=[
        ("images", ("inv_001.png", b"not an image", "image/png")),
        ("ground_truth", ("ground_truth.csv", ground_truth(), "text/csv")),
    ])
    assert bad.status_code == 422


def test_dataset_listing_deletion_and_reference_conflict(client):
    uploaded = upload_dataset(client)
    listed = client.get("/api/datasets", headers=headers("token-a")).json()["datasets"]
    assert any(item["dataset_id"] == uploaded["dataset_id"] for item in listed)
    assert client.get("/api/datasets", headers=headers("token-b")).json()["datasets"] == []
    assert client.delete(f"/api/datasets/{uploaded['dataset_id']}",
                         headers=headers("token-b")).status_code == 404
    assert client.delete(f"/api/datasets/{uploaded['dataset_id']}",
                         headers=headers("token-a")).status_code == 200

    referenced = upload_dataset(client)
    session_id = create_session(client)
    dataset = main_module.datasets.get(referenced["dataset_id"], "tenant-a")
    main_module._bind_dataset(runs.get_session(session_id, "tenant-a"), dataset)
    response = client.delete(f"/api/datasets/{referenced['dataset_id']}",
                             headers=headers("token-a"))
    assert response.status_code == 409


def test_invalid_spec_and_arbitrary_dataset_path_are_rejected(client):
    session_id = create_session(client)
    invalid = valid_spec()
    invalid["unexpected"] = True
    assert client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                       json={"spec": invalid}).status_code == 422
    arbitrary = valid_spec(os.path.abspath("secrets"))
    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": arbitrary})
    assert response.status_code == 422
    assert "paths are not accepted" in response.json()["detail"]


def test_validation_errors_never_echo_submitted_secret(client):
    secret = "super-secret-value-that-must-not-return"
    response = client.post("/api/chat", headers=headers("token-a"), json={
        "message": secret, secret: secret})
    assert response.status_code == 422
    assert secret not in response.text
    assert all(set(item) <= {"type", "loc", "msg"} for item in response.json()["detail"])
    upload = client.post("/api/datasets", headers=headers("token-a"), files=[
        ("images", (f"{secret}.png", b"not an image", "image/png")),
        ("ground_truth", ("ground_truth.csv", ground_truth(secret), "text/csv")),
    ])
    assert upload.status_code == 422
    assert secret not in upload.text


def test_worker_failure_has_explicit_failed_terminal_state(client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr(main_module, "_get_or_create_orchestrator", fail)
    response = client.post("/api/chat", headers=headers("token-a"), json={
        "message": "benchmark invoices", "mode": "real"})
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    for _ in range(50):
        session = runs.get_session(session_id, "tenant-a")
        if session and not session["is_running"]:
            break
        time.sleep(0.01)
    assert session["phase"] == "FAILED"
    assert [event for _, event, _ in session["events"]][-3:] == ["error", "state", "done"]
    assert session["results"] is None


def test_real_tool_assessment_spec_is_accepted_and_reruns_get_new_ids(client, monkeypatch):
    class DeferredThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(main_module, "provider_environment",
                        lambda _tenant: {"OPENROUTER_API_KEY": "test-provider"})
    session_id = create_session(client)
    body = {"mode": "real", "spec": {
        "benchmark_type": "tool_assessment", "category": "developer tools",
        "objective": "Compare implementation effort",
        "candidates": [{"name": "vendor", "display_name": "Vendor",
                        "docs_url": "https://example.com/docs", "pricing_url": "",
                        "kind": "saas"}],
    }}
    first = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"), json=body)
    assert first.status_code == 200
    first_id = first.json()["run_id"]
    runs.finish_run(session_id, run_id=first_id)
    second = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"), json=body)
    assert second.status_code == 200
    assert second.json()["run_id"] != first_id
    session = client.get(f"/api/sessions/{session_id}", headers=headers("token-a")).json()
    assert session["latest_run_id"] == second.json()["run_id"]
    assert len(session["run_history"]) == 2


def test_concurrent_run_quota_returns_429(client, monkeypatch):
    class DeferredThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(main_module, "provider_environment",
                        lambda _tenant: {"OPENROUTER_API_KEY": "test-provider"})
    monkeypatch.setattr(runs, "MAX_CONCURRENT_RUNS", 1)
    body = {"mode": "real", "spec": {
        "benchmark_type": "tool_assessment", "category": "tools", "objective": "compare",
        "candidates": [{"name": "vendor", "display_name": "Vendor",
                        "docs_url": "https://example.com/docs", "kind": "saas"}],
    }}
    first = create_session(client)
    second = create_session(client)
    assert client.post(f"/api/sessions/{first}/run", headers=headers("token-a"), json=body).status_code == 200
    response = client.post(f"/api/sessions/{second}/run", headers=headers("token-a"), json=body)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_rejected_chat_does_not_mutate_mode_or_messages(client):
    session_id = create_session(client)
    assert runs.begin_run(session_id)
    before = runs.get_session(session_id, "tenant-a")
    response = client.post("/api/chat", headers=headers("token-a"), json={
        "session_id": session_id, "message": "must not persist", "mode": "real"})
    after = runs.get_session(session_id, "tenant-a")
    assert response.status_code == 409
    assert after["mode"] == before["mode"]
    assert after["messages"] == before["messages"]


def test_each_run_orchestrator_uses_fresh_deployment_secret_snapshot(client, monkeypatch):
    captured = []

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs["provider_env"])

    import engine.agent
    monkeypatch.setattr(engine.agent, "Orchestrator", FakeOrchestrator)
    session_id = create_session(client)
    session = runs.get_session(session_id, "tenant-a")
    monkeypatch.setenv("OPENAI_API_KEY", "first-secret")
    one = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "real")
    first = main_module._get_or_create_orchestrator(Identity("tenant-a"), session, one["id"])
    runs.finish_run(session_id, run_id=one["id"])
    monkeypatch.setenv("OPENAI_API_KEY", "second-secret")
    two = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "real")
    second = main_module._get_or_create_orchestrator(Identity("tenant-a"), session, two["id"])
    assert first is not second
    assert captured[-2]["OPENAI_API_KEY"] == "first-secret"
    assert captured[-1]["OPENAI_API_KEY"] == "second-secret"
    runs.finish_run(session_id, run_id=two["id"])


def test_persist_run_preserves_engine_provenance_wrapper(client):
    session_id = create_session(client)
    claimed = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "real")
    directory = runs.run_dir(claimed["id"], "tenant-a")
    with open(os.path.join(directory, "metrics.json"), "w", encoding="utf-8") as handle:
        json.dump({"provenance": "measured", "metrics": {"engine": {"score": 1}}}, handle)
    runs.persist_run(claimed["id"], metrics={"wrong": {}}, provenance="synthetic")
    result = runs.load_run_results(claimed["id"], "tenant-a")
    assert result["provenance"] == "measured"
    assert result["metrics"] == {"engine": {"score": 1}}


def test_running_result_without_metrics_has_pending_provenance(client):
    session_id = create_session(client)
    claimed = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "real")
    result = runs.load_run_results(claimed["id"], "tenant-a")
    assert result["status"] == "running"
    assert result["metrics"] is None
    assert result["provenance"] == "pending"


def test_sqlite_provenance_survives_missing_metrics_artifact(client):
    session_id = create_session(client)
    claimed = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "demo")
    runs.persist_run(claimed["id"], metrics={"engine": {"score": 1}},
                     provenance="synthetic")
    metrics_path = os.path.join(runs.run_dir(claimed["id"], "tenant-a"), "metrics.json")
    os.remove(metrics_path)
    result = runs.load_run_results(claimed["id"], "tenant-a")
    assert result["provenance"] == "synthetic"
    assert result["metrics"] == {"engine": {"score": 1}}


def test_new_event_stream_does_not_stop_at_retained_previous_run_done(client):
    session_id = create_session(client)
    first = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "real")
    runs.emit(session_id, "state", {"phase": "FIRST"}, first["id"])
    runs.emit(session_id, "done", {}, first["id"])
    runs.finish_run(session_id, run_id=first["id"])
    second = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "real")
    runs.emit(session_id, "state", {"phase": "SECOND"}, second["id"])
    runs.emit(session_id, "done", {}, second["id"])
    runs.finish_run(session_id, run_id=second["id"])
    response = client.get(f"/api/sessions/{session_id}/events", headers=headers("token-a"))
    assert response.status_code == 200
    assert response.text.count("event: done") == 2
    assert response.text.index('"phase": "SECOND"') > response.text.index("event: done")


def test_public_api_responses_never_expose_server_dataset_paths(client):
    uploaded = upload_dataset(client)
    internal = main_module.datasets.get(uploaded["dataset_id"], "tenant-a")
    session_id = create_session(client)
    main_module._bind_dataset(runs.get_session(session_id, "tenant-a"), internal)
    runs.emit(session_id, "artifact", {"kind": "trace", "path": internal.path,
                                       "detail": f"written to {internal.path} and /etc/passwd"})
    responses = [
        client.get("/api/datasets", headers=headers("token-a")),
        client.get(f"/api/sessions/{session_id}", headers=headers("token-a")),
    ]
    encoded_path = json.dumps(internal.path)[1:-1]
    for response in responses:
        assert response.status_code == 200
        assert internal.path not in response.text
        assert encoded_path not in response.text
        assert "/etc/passwd" not in response.text


def test_provider_base_url_policy_is_allowlisted_and_private_safe(monkeypatch):
    public_answer = [(2, 1, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(main_module.socket, "getaddrinfo", lambda *_args, **_kwargs: public_answer)
    main_module._validate_provider_setting("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    with pytest.raises(Exception) as unknown:
        main_module._validate_provider_setting("CUSTOM_BASE_URL", "https://attacker.example/v1")
    assert "attacker.example" not in str(unknown.value)
    monkeypatch.setenv("PROOFBENCH_PROVIDER_HOST_ALLOWLIST", "*.vendor.example")
    main_module._validate_provider_setting("CUSTOM_BASE_URL", "https://api.vendor.example/v1")
    with pytest.raises(Exception):
        main_module._validate_provider_setting("CUSTOM_BASE_URL", "https://api.vendor.example:8443/v1")
    monkeypatch.setattr(main_module.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (2, 1, 6, "", ("169.254.169.254", 443))])
    with pytest.raises(Exception) as private:
        main_module._validate_provider_setting("CUSTOM_BASE_URL", "https://api.vendor.example/v1")
    assert "api.vendor.example" not in str(private.value)
    with pytest.raises(Exception):
        main_module._validate_provider_setting(
            "CUSTOM_BASE_URL", "https://user:password@api.vendor.example/v1")


def test_deletion_queue_retries_and_keeps_dataset_tombstone(client, monkeypatch):
    uploaded = upload_dataset(client)
    dataset_id = uploaded["dataset_id"]
    dataset = runs.begin_dataset_delete(dataset_id, "tenant-a")
    assert dataset is not None
    real_rmtree = runs.shutil.rmtree

    def fail_delete(_path):
        raise PermissionError("busy")

    monkeypatch.setattr(runs.shutil, "rmtree", fail_delete)
    report = runs.process_deletion_queue([main_module.UPLOADS_DIR])
    assert report["failed"] == 1
    assert runs.STORE.get_deleting_dataset(dataset_id, "tenant-a") is not None
    with runs.STORE.connect() as connection:
        queue = connection.execute(
            "SELECT attempts,last_error,status FROM deletion_queue WHERE resource_kind='dataset' "
            "AND resource_id=?", (dataset_id,)).fetchone()
        assert dict(queue) == {"attempts": 1, "last_error": "PermissionError", "status": "pending"}
        connection.execute("UPDATE deletion_queue SET next_attempt_at=? WHERE resource_id=?",
                           ("2000-01-01T00:00:00Z", dataset_id))
    monkeypatch.setattr(runs.shutil, "rmtree", real_rmtree)
    assert runs.process_deletion_queue([main_module.UPLOADS_DIR]) == {"deleted": 1, "failed": 0}
    assert runs.STORE.get_deleting_dataset(dataset_id, "tenant-a") is None


def test_metrics_are_authenticated_fixed_cardinality_operational_summary(client):
    assert client.get("/api/metrics").status_code == 401
    response = client.get("/api/metrics", headers=headers("token-a"))
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert {"requests", "duration_ms_average", "auth_rejections", "quota_rejections",
            "active_runs", "failed_runs", "retention_failures",
            "sandbox_reconciliation_failures", "deletions"} <= set(metrics)
    assert "tenant-a" not in response.text


@pytest.fixture()
def retention_probe(tmp_path, monkeypatch):
    """Record retention calls without touching real state."""
    calls: dict[str, list] = {"cleanup": [], "deletion": []}
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main_module, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(runs, "cleanup_expired",
                        lambda days: calls["cleanup"].append(days) or {})
    monkeypatch.setattr(runs, "process_deletion_queue",
                        lambda roots: calls["deletion"].append(roots) or {"failed": 0})
    return calls


def test_retention_zero_disables_automatic_expiry(retention_probe, monkeypatch):
    monkeypatch.setenv("PROOFBENCH_RETENTION_DAYS", "0")

    main_module._run_retention()

    assert retention_probe["cleanup"] == []
    # Explicitly requested deletions must still be processed when expiry is off.
    assert len(retention_probe["deletion"]) == 1


def test_retention_defaults_to_no_automatic_expiry(retention_probe, monkeypatch):
    monkeypatch.delenv("PROOFBENCH_RETENTION_DAYS", raising=False)

    main_module._run_retention()

    assert retention_probe["cleanup"] == []


def test_retention_positive_value_still_expires(retention_probe, monkeypatch):
    monkeypatch.setenv("PROOFBENCH_RETENTION_DAYS", "30")

    main_module._run_retention()

    assert retention_probe["cleanup"] == [30]


@pytest.mark.parametrize("value", ["-1", "3651"])
def test_retention_rejects_out_of_range_values(retention_probe, monkeypatch, value):
    monkeypatch.setenv("PROOFBENCH_RETENTION_DAYS", value)

    with pytest.raises(RuntimeError, match="PROOFBENCH_RETENTION_DAYS"):
        main_module._run_retention()

    assert retention_probe["cleanup"] == []


def test_lifespan_reconciles_sandbox_ledger_before_serving(tmp_path, monkeypatch):
    calls = []
    # Like the other authenticated-mode tests: an operator .env may have opted
    # into the tokenless local profile, which is mutually exclusive with keys.
    monkeypatch.delenv("PROOFBENCH_INSECURE_DEV", raising=False)
    monkeypatch.setenv("PROOFBENCH_API_KEYS", json.dumps({"tenant-a": TOKEN_A}))

    class FakePool:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def reconcile_orphans(self):
            calls.append(("reconcile", None))
            return {"deleted": [], "failures": []}

    import engine.sandbox_pool
    monkeypatch.setattr(engine.sandbox_pool, "SandboxPool", FakePool)
    monkeypatch.setattr(main_module, "_run_retention", lambda: None)
    monkeypatch.setattr(runs, "acquire_leader", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runs, "active_job_contract", lambda: {
        "active_owner_keys": [], "active_worker_ids": [], "orphan_before": "2000-01-01T00:00:00Z"})
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    monkeypatch.setenv("PROOFBENCH_RECONCILE_SANDBOXES_ON_STARTUP", "1")
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    with TestClient(app):
        assert calls[-1][0] == "reconcile"
    assert calls[0][1]["size"] == 0
    assert calls[0][1]["ledger_path"].endswith("sandbox_ledger.sqlite3")


def test_begin_run_is_atomic(client):
    session_id = create_session(client)
    barrier = threading.Barrier(16)
    outcomes: list[bool] = []

    def begin():
        barrier.wait()
        outcomes.append(runs.begin_run(session_id))

    threads = [threading.Thread(target=begin) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count(True) == 1


def test_events_and_messages_are_bounded(client, monkeypatch):
    runs.STORE.max_events = 5
    runs.STORE.max_messages = 3
    session_id = create_session(client)
    for index in range(12):
        runs.emit(session_id, "delta", {"text": str(index)})
        runs.add_message(session_id, "assistant", str(index))
    session = runs.get_session(session_id, "tenant-a")
    assert len(session["events"]) == 5
    assert len(session["messages"]) == 3
    assert [event[0] for event in session["events"]] == list(range(7, 12))


def test_event_secrets_are_redacted_before_persistence(client):
    session_id = create_session(client)
    provider_credentials.set("tenant-a", "CUSTOM_API_KEY", "provider-secret-value")
    runs.add_message(session_id, "user", "my key is provider-secret-value")
    runs.emit(session_id, "artifact", {"kind": "trace", "detail":
              "Authorization: Bearer token-a provider-secret-value", "api_key": "another-secret"})
    session = runs.get_session(session_id, "tenant-a")
    with runs.STORE.connect() as connection:
        persisted = " ".join(row[0] for row in connection.execute(
            "SELECT data_json FROM events WHERE session_id=?", (session_id,)).fetchall())
        persisted += " " + " ".join(row[0] for row in connection.execute(
            "SELECT text FROM messages WHERE session_id=?", (session_id,)).fetchall())
    assert "token-a" not in persisted
    assert "provider-secret-value" not in persisted
    assert "another-secret" not in persisted
    assert "[REDACTED]" in session["events"][-1][2]["detail"]
    assert "provider-secret-value" not in session["messages"][-1]["text"]


def test_result_paths_are_confined_and_readiness_is_separate(client):
    assert client.get("/api/ready", headers=headers("token-a")).status_code == 200
    assert client.get("/api/ready").status_code == 401
    assert client.get("/api/runs/..%2F..%2Fetc/results",
                      headers=headers("token-a")).status_code in {404, 422}
    with pytest.raises(ValueError):
        runs._run_dir("../../etc")


# --- Authentication mode is exactly one of two profiles -----------------------

def _auth_env(monkeypatch, *, insecure_dev: str | None, keys: str | None) -> None:
    for name, value in (("PROOFBENCH_INSECURE_DEV", insecure_dev),
                        ("PROOFBENCH_API_KEYS", keys)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_mixed_auth_modes_are_rejected_at_configuration(monkeypatch):
    _auth_env(monkeypatch, insecure_dev="1", keys=json.dumps({"tenant-a": TOKEN_A}))
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        check_auth_mode()
    assert auth_is_configured() is False


def test_no_auth_mode_is_rejected_at_configuration(monkeypatch):
    _auth_env(monkeypatch, insecure_dev=None, keys=None)
    with pytest.raises(RuntimeError, match="no authentication mode"):
        check_auth_mode()
    assert auth_is_configured() is False


def test_authenticated_mode_without_keys_is_rejected(monkeypatch):
    # An explicit opt-out of the local profile with an empty key map authenticates
    # nothing, so it must not be mistaken for a configured deployment.
    _auth_env(monkeypatch, insecure_dev="0", keys="   ")
    with pytest.raises(RuntimeError, match="no authentication mode"):
        check_auth_mode()
    assert auth_is_configured() is False


@pytest.mark.parametrize("insecure_dev,keys", [
    ("1", '{"tenant-a": "%s"}' % TOKEN_A),
    (None, None),
    ("0", ""),
])
def test_startup_refuses_to_serve_a_misconfigured_auth_mode(
    tmp_path, monkeypatch, insecure_dev, keys
):
    _auth_env(monkeypatch, insecure_dev=insecure_dev, keys=keys)
    _isolate_storage(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_mixed_auth_modes_never_resolve_to_the_local_identity(monkeypatch):
    _auth_env(monkeypatch, insecure_dev="1", keys=json.dumps({"tenant-a": TOKEN_A}))
    # Without the mode check this would have returned the tokenless local
    # tenant, silently granting write access to an unauthenticated caller.
    with pytest.raises(HTTPException) as excinfo:
        authenticate_token("")
    assert excinfo.value.status_code == 503
    assert TOKEN_A not in str(excinfo.value.detail)


def test_local_profile_still_authenticates_without_a_token(monkeypatch):
    _auth_env(monkeypatch, insecure_dev="1", keys=None)
    check_auth_mode()
    assert auth_is_configured() is True
    assert authenticate_token("").tenant_id == "local-dev"


# --- POST /api/auth/session is bounded and throttled --------------------------

def test_auth_session_rejects_an_oversize_declared_body(client):
    payload = json.dumps({"token": "x" * (main_module.MAX_AUTH_BODY_BYTES + 1)})
    response = client.post("/api/auth/session", content=payload,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert "too large" in response.text


def test_auth_session_rejects_an_oversize_undeclared_body(client):
    # A chunked upload declares no Content-Length, so only the streaming bound
    # can stop it. Without it the whole payload would already be in memory.
    def chunks():
        for _ in range(4):
            yield b"y" * 8192

    response = client.post("/api/auth/session", content=chunks(),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_auth_session_accepts_a_body_within_the_bound(client):
    response = client.post("/api/auth/session", json={"token": TOKEN_A})
    assert response.status_code == 200


def test_auth_session_throttles_repeated_failed_logins(client, monkeypatch):
    monkeypatch.setattr(main_module, "_login_failures", {})
    statuses = [client.post("/api/auth/session",
                            headers={"Authorization": "Bearer wrong-token-value"}).status_code
                for _ in range(main_module._LOGIN_FAILURE_LIMIT + 2)]
    assert statuses[0] == 401
    assert statuses[-1] == 429
    assert statuses.count(429) >= 2
    # A throttled response is a bare refusal: no token, valid or attempted, and
    # no configuration detail may be reflected back to the caller.
    throttled = client.post("/api/auth/session",
                            headers={"Authorization": "Bearer wrong-token-value"})
    assert throttled.headers["Retry-After"]
    assert TOKEN_A not in throttled.text
    assert "wrong-token-value" not in throttled.text


def test_login_throttle_counts_only_credential_rejections(client, monkeypatch):
    monkeypatch.setattr(main_module, "_login_failures", {})
    for _ in range(main_module._LOGIN_FAILURE_LIMIT + 2):
        assert client.post("/api/auth/session", json={"token": TOKEN_A}).status_code == 200


def test_login_throttle_client_store_has_a_hard_capacity(monkeypatch):
    monkeypatch.setattr(main_module, "_login_failures", {})
    monkeypatch.setattr(main_module, "_login_overflow_failures", [])

    for index in range(main_module._LOGIN_CLIENT_LIMIT + 25):
        main_module._record_login_failure(f"198.51.100.{index}")

    assert len(main_module._login_failures) == main_module._LOGIN_CLIENT_LIMIT
    assert len(main_module._login_overflow_failures) <= main_module._LOGIN_FAILURE_LIMIT
    assert main_module._login_throttled("203.0.113.250") is True


def test_local_profile_logins_are_not_throttled(local_client, monkeypatch):
    monkeypatch.setattr(main_module, "_login_failures", {})
    for _ in range(main_module._LOGIN_FAILURE_LIMIT + 2):
        assert local_client.post("/api/auth/session").status_code == 200


def test_auth_status_remains_public_and_reports_the_local_profile(local_client):
    response = local_client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json()["auth_mode"] == "local"
