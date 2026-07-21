"""Write admission is real-only and fails closed; legacy runs stay readable."""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from server import runs
from server.main import app
from server.security import provider_credentials
from server.state import SQLiteStore
from server.storage import DatasetRegistry
import server.main as main_module
import server.storage as storage_module

from server.test_backend_hardening import (  # noqa: F401  (client fixture)
    TOKEN_A, TOKEN_B, client, create_session, ground_truth, headers, png_bytes,
    upload_dataset, valid_spec,
)


def assessment_spec() -> dict:
    return {
        "benchmark_type": "tool_assessment",
        "category": "CRM",
        "objective": "sync accounts nightly",
        "candidates": [{"name": "alpha", "display_name": "Alpha",
                        "docs_url": "https://example.com/docs", "kind": "saas"}],
    }


# --------------------------------------------------------------- mode admission

def test_omitted_mode_is_real_for_chat_and_run(client):
    """A client that sends no mode gets a real run, not a simulated one."""
    dataset = upload_dataset(client)
    session_id = create_session(client)

    chat = client.post("/api/chat", headers=headers("token-a"), json={
        "session_id": session_id, "message": "compare OCR tools",
        "dataset_id": dataset["dataset_id"]})
    assert chat.status_code == 200

    stored = runs.get_session(session_id, "tenant-a")
    assert stored["mode"] == "real"


def test_explicit_demo_is_rejected_before_any_mutation(client):
    """422 at the schema boundary: no session, run, or dataset state changes."""
    dataset = upload_dataset(client)
    session_id = create_session(client)
    before = runs.get_session(session_id, "tenant-a")

    chat = client.post("/api/chat", headers=headers("token-a"), json={
        "session_id": session_id, "message": "benchmark",
        "dataset_id": dataset["dataset_id"], "mode": "demo"})
    assert chat.status_code == 422

    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    run = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                      json={"spec": spec, "mode": "demo"})
    assert run.status_code == 422

    after = runs.get_session(session_id, "tenant-a")
    assert after["latest_run_id"] == before["latest_run_id"]
    assert not after["is_running"]
    # No run row was allocated for the rejected request.
    assert after["run_history"] == []


def test_extraction_fields_must_match_the_deterministic_evaluator_schema(client):
    dataset = upload_dataset(client)
    session_id = create_session(client)
    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    spec["fields"] = ["amount"]

    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": spec})

    assert response.status_code == 422
    assert runs.get_session(session_id, "tenant-a")["run_history"] == []


def test_demo_mode_cannot_be_smuggled_through_the_spec(client):
    """BenchmarkSpec has no client-writable field that can flip execution."""
    dataset = upload_dataset(client)
    session_id = create_session(client)
    for smuggled in ("demo_mode", "trusted_adapter_token"):
        spec = valid_spec()
        spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
        spec[smuggled] = True
        response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                               json={"spec": spec})
        assert response.status_code == 422, smuggled


def test_candidate_level_trusted_token_is_rejected(client):
    dataset = upload_dataset(client)
    session_id = create_session(client)
    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    spec["candidates"][0]["trusted_adapter_token"] = "forged"
    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": spec})
    assert response.status_code == 422


def test_assessment_run_also_refuses_demo(client):
    session_id = create_session(client)
    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": assessment_spec(), "mode": "demo"})
    assert response.status_code == 422


def test_spec_capabilities_are_rejected_before_run_admission(client, monkeypatch):
    monkeypatch.setattr(main_module, "provider_environment", lambda _tenant: {})
    session_id = create_session(client)
    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": assessment_spec()})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "provider_capability_unavailable"
    assert [item["capability"] for item in detail["capabilities"]] == ["assessment"]
    assert "API_KEY" in json.dumps(detail)
    assert not runs.get_session(session_id, "tenant-a")["is_running"]
    assert runs.get_session(session_id, "tenant-a")["run_history"] == []


def test_generated_extraction_requires_codegen_before_run_admission(client, monkeypatch):
    monkeypatch.setattr(main_module, "provider_environment",
                        lambda _tenant: {"OPENAI_API_KEY": "hidden-orchestration"})
    dataset = upload_dataset(client)
    session_id = create_session(client)
    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    spec["candidates"] = [{"name": "paddleocr", "docs_url": "https://example.com/paddleocr",
                           "kind": "local_tool", "use_fallback": False}]
    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": spec})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert [item["capability"] for item in detail["capabilities"]] == ["codegen"]
    assert "hidden-orchestration" not in response.text
    assert runs.get_session(session_id, "tenant-a")["run_history"] == []


def test_builtin_extraction_does_not_require_an_orchestration_provider(client, monkeypatch):
    class DeferredThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(main_module, "provider_environment", lambda _tenant: {})
    dataset = upload_dataset(client)
    session_id = create_session(client)
    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": spec})

    assert response.status_code == 200
    runs.finish_run(session_id, run_id=response.json()["run_id"])


def test_late_report_failure_persists_authoritative_metrics_and_completes(client, monkeypatch):
    class FailingAfterMetrics:
        def __init__(self, run_id):
            self.run_id = run_id
            self.ctx = type("Context", (), {"citations": [], "env_passthrough": {}})()

        def run_benchmark(self, _spec):
            directory = runs.run_dir(self.run_id, "tenant-a")
            with open(os.path.join(directory, "metrics.json"), "w", encoding="utf-8") as handle:
                json.dump({"provenance": "measured", "metrics": {"paddleocr": {"score": 91}}}, handle)
            raise RuntimeError("pdf renderer failed")

    monkeypatch.setattr(main_module, "provider_environment",
                        lambda _tenant: {"OPENROUTER_API_KEY": "hidden-provider"})
    monkeypatch.setattr(main_module, "_get_or_create_orchestrator",
                        lambda _identity, _session, run_id: FailingAfterMetrics(run_id))
    dataset = upload_dataset(client)
    session_id = create_session(client)
    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    spec["candidates"] = [{"name": "paddleocr", "docs_url": "https://example.com/paddleocr",
                           "kind": "local_tool", "use_fallback": False}]

    started = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"), json={"spec": spec})

    assert started.status_code == 200
    run_id = started.json()["run_id"]
    for _ in range(100):
        if not runs.get_session(session_id, "tenant-a")["is_running"]:
            break
        import time
        time.sleep(0.01)
    result = client.get(f"/api/runs/{run_id}/results", headers=headers("token-a")).json()
    assert result["status"] == "completed"
    assert result["provenance"] == "measured"
    assert result["metrics"] == {"paddleocr": {"score": 91}}
    session = runs.get_session(session_id, "tenant-a")
    assert any(event == "artifact" and not data.get("available", True)
               for _seq, event, data in session["events"])
    assert client.get(f"/api/runs/{run_id}/report.pdf", headers=headers("token-a")).status_code == 404


def test_normalized_intake_specs_are_accepted_by_the_run_schema():
    """Whatever intake confirms must survive RunRequest, or it is not confirmed.

    Regression: intake emitted the model's raw JSON, so a literal "Open-source"
    pricing_url or a malformed required docs_url produced a spec artifact that
    POST /api/sessions/{id}/run then rejected with 422.
    """
    from pydantic import ValidationError

    from engine.agent import _normalize_intake_spec
    from server.schemas import RunRequest

    raw = {"benchmark_type": "tool_assessment", "category": "CRM sync",
           "objective": "sync accounts nightly",
           "candidates": [{"name": "alpha", "display_name": "Alpha",
                           "docs_url": "https://docs.alpha.example/guide",
                           "pricing_url": "Open-source", "kind": "saas"}]}
    normalized = _normalize_intake_spec(raw, dataset_available=False)
    assert normalized["candidates"][0]["pricing_url"] == ""
    assert RunRequest(spec=normalized).spec.benchmark_type == "tool_assessment"

    # The same payload with an unusable required docs_url yields no spec at all.
    broken = json.loads(json.dumps(raw))
    broken["candidates"][0]["docs_url"] = "docs.alpha.example/guide"
    assert _normalize_intake_spec(broken, dataset_available=False) is None
    with pytest.raises(ValidationError):
        RunRequest(spec=broken)


def test_corrupt_metrics_artifact_never_completes_a_failed_run(client, monkeypatch):
    """Only a well-formed measured artifact rescues a late failure."""
    class FailingWithCorruptArtifact:
        def __init__(self, run_id):
            self.run_id = run_id
            self.ctx = type("Context", (), {"citations": []})()

        def run_benchmark(self, _spec):
            directory = runs.run_dir(self.run_id, "tenant-a")
            with open(os.path.join(directory, "metrics.json"), "w", encoding="utf-8") as handle:
                handle.write("[]")
            raise RuntimeError("pdf renderer failed")

    monkeypatch.setattr(main_module, "provider_environment",
                        lambda _tenant: {"OPENROUTER_API_KEY": "hidden-provider"})
    monkeypatch.setattr(main_module, "_get_or_create_orchestrator",
                        lambda _identity, _session, run_id: FailingWithCorruptArtifact(run_id))
    dataset = upload_dataset(client)
    session_id = create_session(client)
    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    spec["candidates"] = [{"name": "paddleocr", "docs_url": "https://example.com/paddleocr",
                           "kind": "local_tool", "use_fallback": False}]

    started = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                          json={"spec": spec})

    assert started.status_code == 200
    run_id = started.json()["run_id"]
    for _ in range(100):
        if not runs.get_session(session_id, "tenant-a")["is_running"]:
            break
        import time
        time.sleep(0.01)
    result = client.get(f"/api/runs/{run_id}/results", headers=headers("token-a")).json()
    assert result["status"] == "failed"
    assert result["metrics"] is None
    assert result["provenance"] == "pending"


# ------------------------------------------------------ built-in credential gate

def test_builtin_candidate_without_credentials_is_an_explicit_preflight_error(
        client, monkeypatch):
    """Missing keys produce candidate_unavailable, never a silent fallback."""
    monkeypatch.setattr(main_module, "provider_environment", lambda _tenant: {})
    dataset = upload_dataset(client)
    session_id = create_session(client)
    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    spec["candidates"] = [{"name": "doubleword", "kind": "hosted_api", "use_fallback": True}]

    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": spec})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "candidate_unavailable"
    assert detail["candidates"][0]["name"] == "doubleword"
    assert "DOUBLEWORD_API_KEY" in detail["candidates"][0]["missing"]

    # Failing closed means nothing was allocated.
    assert not runs.get_session(session_id, "tenant-a")["is_running"]


def test_preflight_reports_names_never_values(client, monkeypatch):
    monkeypatch.setattr(main_module, "provider_environment",
                        lambda _tenant: {"DOUBLEWORD_API_KEY": "hidden-secret-value"})
    dataset = upload_dataset(client)
    session_id = create_session(client)
    spec = valid_spec()
    spec["dataset"] = {"dataset_id": dataset["dataset_id"]}
    spec["candidates"] = [{"name": "doubleword", "kind": "hosted_api", "use_fallback": True}]

    response = client.post(f"/api/sessions/{session_id}/run", headers=headers("token-a"),
                           json={"spec": spec})
    assert response.status_code == 422
    assert "hidden-secret-value" not in response.text
    assert "DOUBLEWORD_MODEL" in response.json()["detail"]["candidates"][0]["missing"]


def test_local_builtin_needs_no_credentials(client, monkeypatch):
    monkeypatch.setattr(main_module, "provider_environment", lambda _tenant: {})
    assert main_module._unavailable_builtin_candidates(valid_spec(), {}) == []


def test_generated_candidate_named_like_a_builtin_is_not_authorized(client, monkeypatch):
    """A non-builtin name is never entitled, so it never blocks or grants."""
    spec = valid_spec()
    spec["candidates"] = [{"name": "doubleword_pro", "kind": "hosted_api", "use_fallback": True}]
    assert main_module._unavailable_builtin_candidates(spec, {}) == []
    assert main_module._requested_builtins(spec) == []


def test_authorization_tokens_stay_out_of_the_persisted_spec(client, monkeypatch):
    """Capability tokens live only in the private execution copy."""
    from engine.agent import TRUSTED_ADAPTER_TOKEN_FIELD, Orchestrator

    env = {"DOUBLEWORD_API_KEY": "hidden", "DOUBLEWORD_MODEL": "test-model"}
    orchestrator = Orchestrator("auth", str(runs.RUNS_DIR), lambda *_a: None, provider_env=env)
    spec = {"candidates": [{"name": "doubleword", "use_fallback": True},
                           {"name": "tesseract", "use_fallback": True}]}

    execution = main_module._authorize_builtin_candidates(orchestrator, spec)

    assert all(TRUSTED_ADAPTER_TOKEN_FIELD not in item for item in spec["candidates"])
    tokens = {item["name"]: item.get(TRUSTED_ADAPTER_TOKEN_FIELD)
              for item in execution["candidates"]}
    assert tokens["doubleword"] and tokens["tesseract"]
    assert tokens["doubleword"] != tokens["tesseract"]


def test_execution_spec_carries_the_official_builtin_urls(client, monkeypatch):
    """DOCS_INTEL must never be handed an empty URL for a first-party adapter.

    Regression: the resolved docs_url was applied to the loaded adapter only,
    so a spec that omitted it reached the agent blank and scrape_docs ran
    against an empty address.
    """
    from engine.agent import TRUSTED_ADAPTER_TOKEN_FIELD, Orchestrator

    env = {"OPENAI_API_KEY": "hidden"}
    orchestrator = Orchestrator("urls", str(runs.RUNS_DIR), lambda *_a: None, provider_env=env)
    spec = {"candidates": [{"name": "tesseract", "use_fallback": True},
                           {"name": "openai_vision", "use_fallback": True}]}

    execution = main_module._authorize_builtin_candidates(orchestrator, spec)
    resolved = {item["name"]: item for item in execution["candidates"]}

    assert resolved["tesseract"]["docs_url"] == "https://tesseract-ocr.github.io/tessdoc/"
    assert resolved["openai_vision"]["docs_url"] == (
        "https://platform.openai.com/docs/guides/vision")
    assert resolved["openai_vision"]["pricing_url"] == "https://openai.com/api/pricing/"

    # The client-visible input is untouched, and tokens stay private to the copy.
    assert spec["candidates"] == [{"name": "tesseract", "use_fallback": True},
                                  {"name": "openai_vision", "use_fallback": True}]
    assert all(TRUSTED_ADAPTER_TOKEN_FIELD not in item for item in spec["candidates"])
    assert all(item.get(TRUSTED_ADAPTER_TOKEN_FIELD) for item in execution["candidates"])


def test_a_client_supplied_docs_url_is_preserved_over_the_default(client, monkeypatch):
    """The resolution order stays candidate spec first, built-in default second."""
    from engine.agent import Orchestrator

    orchestrator = Orchestrator("urls2", str(runs.RUNS_DIR), lambda *_a: None, provider_env={})
    spec = {"candidates": [{"name": "tesseract", "use_fallback": True,
                            "docs_url": "https://example.invalid/tesseract"}]}

    execution = main_module._authorize_builtin_candidates(orchestrator, spec)

    assert execution["candidates"][0]["docs_url"] == "https://example.invalid/tesseract"


# ------------------------------------------------------------ legacy readability

def test_legacy_synthetic_run_remains_readable_and_labelled_synthetic(client):
    """Runs persisted by an earlier demo-capable version are not rewritten."""
    dataset = upload_dataset(client)
    session_id = create_session(client)
    claimed = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "demo",
                                   dataset["dataset_id"])
    runs.persist_run(claimed["id"], spec=valid_spec(),
                     metrics={"tesseract": {"exact_accuracy": 0.9}},
                     report_md="# historical", citations=[], provenance="synthetic")
    runs.finish_run(session_id, cancelled=False, failed=False, emit_done=False,
                    run_id=claimed["id"])

    results = client.get(f"/api/runs/{claimed['id']}/results", headers=headers("token-a"))
    assert results.status_code == 200
    body = results.json()
    assert body["provenance"] == "synthetic"
    assert body["metrics"]["tesseract"]["exact_accuracy"] == 0.9

    # The durable mode column is retained so the run stays identifiable as historical.
    history = runs.get_session(session_id, "tenant-a")["run_history"]
    assert [item["mode"] for item in history] == ["demo"]


def test_pending_and_unverified_runs_expose_no_metrics(client):
    """A run without immutable measured evidence must not present metrics."""
    dataset = upload_dataset(client)
    session_id = create_session(client)

    pending = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "real",
                                   dataset["dataset_id"])
    body = client.get(f"/api/runs/{pending['id']}/results",
                      headers=headers("token-a")).json()
    assert body["provenance"] == "pending"
    assert body["metrics"] is None
    runs.finish_run(session_id, cancelled=False, failed=True, emit_done=False,
                    run_id=pending["id"])

    # Metrics stored without a valid provenance marker are unverified, not measured.
    unverified = runs.begin_benchmark(session_id, "tenant-a", valid_spec(), "real",
                                      dataset["dataset_id"])
    runs.STORE.update_run_artifacts(
        unverified["id"], spec=valid_spec(),
        metrics={"tesseract": {"exact_accuracy": 0.99}},
        report_md="", citations=[], provenance=None)
    body = client.get(f"/api/runs/{unverified['id']}/results",
                      headers=headers("token-a")).json()
    assert body["provenance"] == "unverified"
    assert body["metrics"] is None


# -------------------------------------------------------------- provider status

def test_provider_readiness_reports_status_without_values(client, monkeypatch):
    monkeypatch.setattr(main_module, "provider_environment", lambda _tenant: {
        "DAYTONA_API_KEY": "hidden-daytona-value",
        "OPENAI_API_KEY": "hidden-openai-value",
        "DOUBLEWORD_API_KEY": "hidden-doubleword-value",
    })
    for name in ("DAYTONA_API_KEY", "OPENAI_API_KEY", "DOUBLEWORD_API_KEY",
                 "DOUBLEWORD_MODEL", "DEEPSEEK_API_KEY", "OXYLABS_USERNAME",
                 "OXYLABS_PASSWORD", "NOSANA_API_KEY", "NOSANA_BASE_URL", "NOSANA_MODEL"):
        monkeypatch.delenv(name, raising=False)

    response = client.get("/api/providers", headers=headers("token-a"))
    assert response.status_code == 200
    assert "hidden-daytona-value" not in response.text
    body = response.json()
    assert body["mode"] == "real"

    by_name = {item["provider"]: item for item in body["providers"]}
    assert by_name["daytona"]["status"] == "ready"
    assert by_name["openai"]["status"] == "ready"
    # One of two required names present.
    assert by_name["doubleword"]["status"] == "partial"
    assert by_name["doubleword"]["missing"] == ["DOUBLEWORD_MODEL"]
    assert by_name["oxylabs"]["status"] == "missing"
    assert body["run_ready"] is True


def test_provider_readiness_blocks_when_no_orchestration_provider_is_configured(
    client, monkeypatch
):
    """Orchestration is a capability, not one vendor: losing every LLM key blocks."""
    monkeypatch.setattr(main_module, "provider_environment", lambda _tenant: {})
    for name in ("DAYTONA_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
                 "MOONSHOT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    body = client.get("/api/providers", headers=headers("token-a")).json()
    assert body["run_ready"] is False
    assert set(body["blocked_by"]) == {"daytona", "orchestration"}


def test_openrouter_alone_satisfies_every_llm_capability(client, monkeypatch):
    """OpenRouter must cover orchestration, assessment, and codegen on its own."""
    monkeypatch.setattr(main_module, "provider_environment", lambda _tenant: {})
    for name in ("OPENAI_API_KEY", "MOONSHOT_API_KEY", "DEEPSEEK_API_KEY",
                 "DOUBLEWORD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DAYTONA_API_KEY", "daytona-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")

    body = client.get("/api/providers", headers=headers("token-a")).json()

    by_capability = {item["capability"]: item for item in body["capabilities"]}
    for capability in ("orchestration", "assessment", "codegen"):
        assert by_capability[capability]["status"] == "ready"
        assert by_capability[capability]["selected"] == "openrouter"
    assert body["blocked_by"] == []
    assert body["run_ready"] is True
    by_name = {item["provider"]: item for item in body["providers"]}
    assert by_name["openrouter"]["status"] == "ready"
    # Secret-free: the endpoint reports names and status, never values.
    assert "openrouter-token" not in json.dumps(body)


def test_openrouter_settings_are_listed_but_never_sandbox_eligible(client, monkeypatch):
    """OPENROUTER_ appears in the provider snapshot and in the sandbox deny set."""
    from engine.agent import NEVER_SANDBOX_PREFIXES
    from engine.builtin_adapters import SANDBOX_ELIGIBLE_CREDENTIALS

    assert "OPENROUTER_" in NEVER_SANDBOX_PREFIXES
    assert not any(name.startswith("OPENROUTER_") for name in SANDBOX_ELIGIBLE_CREDENTIALS)

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    body = client.get("/api/settings/provider-keys", headers=headers("token-a")).json()
    listed = {item["env"] for item in body["keys"]}
    assert "OPENROUTER_API_KEY" in listed
    assert "openrouter-token" not in json.dumps(body)


def test_provider_readiness_requires_authentication(client):
    assert client.get("/api/providers").status_code == 401
