"""Focused regression tests for engine isolation and fail-closed behavior."""

from __future__ import annotations

import json
import importlib
import shutil
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine import docs_intel, evaluate, pdf_report, tool_assessment
from engine.agent import Orchestrator
from engine.candidates.base import Candidate
from engine.evaluate import cer, evaluate_results
from engine.network_security import (
    OutboundURLPolicy,
    secure_async_httpx_client,
    secure_httpx_client,
    validate_external_url,
)
from engine.sandbox_pool import SandboxHandle, SandboxPool
from engine.tools import RunContext, append_result_record, dispatch_tool


class FakeSandbox:
    def __init__(self, output: str = ""):
        self.output = output
        self.deleted = 0
        self.code = ""
        self.uploads: list[tuple[str, str]] = []
        self.process = self
        self.fs = self

    def exec(self, _cmd, timeout=None):
        return self.output

    def code_run(self, code, timeout=None):
        self.code = code
        return self.output

    def upload_file(self, local_path, remote_path):
        self.uploads.append((local_path, remote_path))

    def delete(self):
        self.deleted += 1


class ExplodingSandbox(FakeSandbox):
    def code_run(self, _code, timeout=None):
        raise RuntimeError(self.output)


class LedgerClient:
    def __init__(self):
        self.sandboxes = {}
        self.deleted = []
        self.fail_get = set()
        self.list_called = False

    def create(self, *_args, **_kwargs):
        sandbox = FakeSandbox()
        sandbox.id = f"remote-{len(self.sandboxes) + 1}"
        self.sandboxes[sandbox.id] = sandbox
        return sandbox

    def get(self, sandbox_id):
        if sandbox_id in self.fail_get:
            raise RuntimeError("lookup failed")
        return self.sandboxes[sandbox_id]

    def delete(self, sandbox):
        self.deleted.append(sandbox.id)
        self.sandboxes.pop(sandbox.id, None)

    def list(self):
        self.list_called = True
        raise AssertionError("reconciliation must not list unowned sandboxes")


class LocalStyleSandbox(FakeSandbox):
    """Matches the lightweight filesystem shim used by integration_test.py."""

    def __init__(self):
        super().__init__("local-ok")
        self.fs = self

    upload_file = None

    def upload(self, local_path, remote_path):
        self.uploads.append((local_path, remote_path))


class LocalStylePool(SandboxPool):
    def __init__(self, sandbox):
        super().__init__(size=0)
        self.sandbox = sandbox

    def acquire(self, label):
        return SandboxHandle(id=f"{label}-local", label=label, sandbox=self.sandbox)

    def release(self, _handle):
        pass

    def destroy_all(self):
        pass


def make_context(tmp_path: Path, sandbox: FakeSandbox, events: list) -> RunContext:
    pool = SandboxPool(size=0)
    pool._create_one = lambda: sandbox
    ctx = RunContext(
        run_id="run-a",
        run_dir=str(tmp_path),
        pool=pool,
        emit=lambda event, data: events.append((event, data)),
        results_path=str(tmp_path / "results.jsonl"),
    )
    ctx.allowed_candidate_names.update({"safe", "used", "tesseract", "alpha"})
    ctx.allowed_doc_ids.add("doc-1")
    return ctx


def test_released_sandbox_is_destroyed_and_never_reused(monkeypatch):
    created: list[FakeSandbox] = []

    def create():
        sandbox = FakeSandbox()
        created.append(sandbox)
        return sandbox

    pool = SandboxPool(size=0)
    monkeypatch.setattr(pool, "_create_one", create)
    first = pool.acquire("first")
    pool.release(first)
    second = pool.acquire("second")

    assert first.sandbox is not second.sandbox
    assert first.sandbox.deleted == 1
    with pytest.raises(ValueError, match="no longer active"):
        pool.exec(first, "true")
    pool.destroy_all()
    assert second.sandbox.deleted == 1


def test_destroy_all_cleans_leased_and_prewarmed_sandboxes(monkeypatch):
    created: list[FakeSandbox] = []
    create_lock = threading.Lock()

    def create():
        with create_lock:
            sandbox = FakeSandbox()
            created.append(sandbox)
            pool._tracked[id(sandbox)] = sandbox
            return sandbox

    pool = SandboxPool(size=3)
    monkeypatch.setattr(pool, "_create_one", create)
    pool.start()
    pool.acquire("leased")
    pool.destroy_all()
    pool.destroy_all()

    assert len(created) == 3
    assert [sandbox.deleted for sandbox in created] == [1, 1, 1]


def test_custom_local_pool_can_own_handles_and_use_upload_shim(tmp_path):
    source = tmp_path / "document.txt"
    source.write_text("fixture", encoding="utf-8")
    sandbox = LocalStyleSandbox()
    pool = LocalStylePool(sandbox)
    handle = pool.acquire("oracle")

    pool.upload(handle, str(source), "images/document.txt")

    assert pool.run_python(handle, "print('ok')") == "local-ok"
    assert sandbox.uploads == [(str(source), "images/document.txt")]


def test_sandbox_ledger_records_and_removes_after_confirmed_delete(tmp_path):
    client = LedgerClient()
    pool = SandboxPool(
        size=0,
        owner_key="run-1",
        ledger_path=str(tmp_path / "ledger.sqlite3"),
        deployment="deploy-a",
    )
    pool._daytona = client
    handle = pool.acquire("alpha")

    assert [entry["sandbox_id"] for entry in pool._ledger.entries()] == [
        handle.sandbox.id
    ]
    pool.release(handle)

    assert client.deleted == [handle.sandbox.id]
    assert pool._ledger.entries() == []


def test_reconcile_orphans_deletes_only_ledgered_ids_and_reports_failures(tmp_path):
    ledger = str(tmp_path / "ledger.sqlite3")
    client = LedgerClient()
    crashed = SandboxPool(
        size=0, owner_key="crashed-run", ledger_path=ledger, deployment="deploy-a"
    )
    crashed._daytona = client
    first = crashed.acquire("alpha").sandbox.id
    second = crashed.acquire("beta").sandbox.id
    client.fail_get.add(second)

    recovery = SandboxPool(
        size=0, owner_key="startup", ledger_path=ledger, deployment="deploy-a"
    )
    recovery._daytona = client
    report = recovery.reconcile_orphans(
        startup_leader=True, now=10**12, minimum_age_seconds=0
    )

    assert report["deleted"] == [
        {"owner_key": "crashed-run", "sandbox_id": first}
    ]
    assert report["failures"][0]["owner_key"] == "crashed-run"
    assert report["failures"][0]["sandbox_id"] == second
    assert report["failures"][0]["error"] == "RuntimeError"
    assert [entry["sandbox_id"] for entry in recovery._ledger.entries()] == [second]
    assert client.list_called is False


def test_reconcile_respects_worker_lease_and_orphan_minimum_age(tmp_path):
    now = [0.0]
    ledger = str(tmp_path / "ledger.sqlite3")
    client = LedgerClient()
    active = SandboxPool(
        size=0,
        owner_key="active-run",
        ledger_path=ledger,
        deployment="deploy-a",
        worker_id="worker-a",
        lease_seconds=60,
        orphan_min_age_seconds=120,
        clock=lambda: now[0],
    )
    active._daytona = client
    sandbox_id = active.acquire("alpha").sandbox.id
    recovery = SandboxPool(
        size=0,
        owner_key="startup",
        ledger_path=ledger,
        deployment="deploy-a",
        worker_id="worker-b",
        lease_seconds=60,
        orphan_min_age_seconds=120,
        clock=lambda: now[0],
    )
    recovery._daytona = client

    not_leader = recovery.reconcile_orphans(now=1000)
    assert not_leader["deleted"] == []
    assert not_leader["skipped"][0]["reason"] == "startup leader coordination required"
    now[0] = 30
    leased = recovery.reconcile_orphans(startup_leader=True)
    assert leased["deleted"] == []
    assert leased["skipped"][0]["reason"] == "active lease"
    now[0] = 61
    young = recovery.reconcile_orphans(startup_leader=True)
    assert young["deleted"] == []
    assert young["skipped"][0]["reason"] == "minimum age"
    now[0] = 121
    deleted = recovery.reconcile_orphans(startup_leader=True)
    assert deleted["deleted"][0]["sandbox_id"] == sandbox_id


def test_reconcile_uses_one_durable_deployment_leader(tmp_path):
    ledger = str(tmp_path / "ledger.sqlite3")
    client = LedgerClient()
    crashed = SandboxPool(
        size=0,
        owner_key="crashed",
        ledger_path=ledger,
        deployment="deploy-a",
        worker_id="crashed-worker",
        clock=lambda: 0,
    )
    crashed._daytona = client
    sandbox_id = crashed.acquire("alpha").sandbox.id
    client.fail_get.add(sandbox_id)
    first = SandboxPool(
        size=0,
        ledger_path=ledger,
        deployment="deploy-a",
        worker_id="startup-a",
        clock=lambda: 2000,
    )
    second = SandboxPool(
        size=0,
        ledger_path=ledger,
        deployment="deploy-a",
        worker_id="startup-b",
        clock=lambda: 2000,
    )
    first._daytona = client
    second._daytona = client

    assert first.reconcile_orphans(
        startup_leader=True, minimum_age_seconds=0
    )["failures"]
    blocked = second.reconcile_orphans(
        startup_leader=True, minimum_age_seconds=0
    )

    assert blocked["deleted"] == []
    assert blocked["failures"] == []
    assert blocked["skipped"] == [{"reason": "startup reconciler lease held"}]


def test_sandbox_ledger_migrates_legacy_rows_without_losing_ownership(tmp_path):
    ledger = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "CREATE TABLE sandboxes (deployment TEXT NOT NULL, owner_key TEXT NOT NULL, "
            "sandbox_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (deployment, sandbox_id))"
        )
        connection.execute(
            "INSERT INTO sandboxes(deployment, owner_key, sandbox_id) VALUES(?,?,?)",
            ("deploy-a", "legacy-run", "legacy-sandbox"),
        )

    pool = SandboxPool(
        size=0,
        ledger_path=str(ledger),
        deployment="deploy-a",
        worker_id="startup",
    )
    entry = pool._ledger.entries()[0]

    assert entry["owner_key"] == "legacy-run"
    assert entry["sandbox_id"] == "legacy-sandbox"
    assert entry["worker_id"] == "legacy"
    assert entry["lease_expires_at"] == 0


def test_llm_python_tool_never_injects_runtime_credentials_and_redacts_output(tmp_path):
    secret = "used-secret-value"
    unused = "unused-secret-value"
    sandbox = FakeSandbox(f"token={secret}; other={unused}")
    events: list = []
    ctx = make_context(tmp_path, sandbox, events)
    ctx.env_passthrough = {
        "USED_API_KEY": secret,
        "UNUSED_API_KEY": unused,
        "PUBLIC_MODEL": "model-a",
    }
    spawned = json.loads(dispatch_tool("spawn_sandbox", {"label": "used"}, ctx))
    result = dispatch_tool(
        "run_python_in_sandbox",
        {
            "id": spawned["id"],
            "code": 'import os\nprint(os.environ["USED_API_KEY"])',
        },
        ctx,
    )

    assert secret not in sandbox.code
    assert unused not in sandbox.code
    assert "PUBLIC_MODEL" not in sandbox.code
    assert secret not in result
    assert unused not in result
    assert all(secret not in json.dumps(data) for _, data in events)
    assert all(unused not in json.dumps(data) for _, data in events)


def test_local_candidate_cannot_request_openai_secret(tmp_path):
    secret = "openai-secret-value"
    sandbox = FakeSandbox("ok")
    ctx = make_context(tmp_path, sandbox, [])
    ctx.env_passthrough = {"OPENAI_API_KEY": secret}
    spawned = json.loads(
        dispatch_tool("spawn_sandbox", {"label": "tesseract"}, ctx)
    )
    dispatch_tool(
        "run_python_in_sandbox",
        {
            "id": spawned["id"],
            "code": 'import os\nprint(os.environ["OPENAI_API_KEY"])',
        },
        ctx,
    )

    assert secret not in sandbox.code
    assert "OPENAI_API_KEY" in sandbox.code


def test_exception_secret_is_redacted_by_value(tmp_path):
    secret = "provider-secret"
    sandbox = ExplodingSandbox(f"remote failure contained {secret}")
    events: list = []
    ctx = make_context(tmp_path, sandbox, events)
    ctx.env_passthrough = {"PROVIDER_API_KEY": secret}
    spawned = json.loads(dispatch_tool("spawn_sandbox", {"label": "safe"}, ctx))
    result = dispatch_tool(
        "run_python_in_sandbox",
        {"id": spawned["id"], "code": "raise RuntimeError('x')"},
        ctx,
    )

    assert secret not in result
    assert "***" in result
    assert all(secret not in json.dumps(data) for _, data in events)


def test_upload_is_confined_to_configured_dataset_root(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "inside.txt").write_text("inside", encoding="utf-8")
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    sandbox = FakeSandbox()
    ctx = make_context(tmp_path, sandbox, [])
    ctx.allowed_dataset_root = str(allowed)
    spawned = json.loads(dispatch_tool("spawn_sandbox", {"label": "safe"}, ctx))

    accepted = json.loads(
        dispatch_tool(
            "upload_files", {"id": spawned["id"], "local_dir": str(allowed)}, ctx
        )
    )
    rejected = json.loads(
        dispatch_tool(
            "upload_files", {"id": spawned["id"], "local_dir": str(outside)}, ctx
        )
    )

    assert accepted["uploaded"] == ["inside.txt"]
    assert "dataset root" in rejected["error"]
    assert len(sandbox.uploads) == 1


def test_model_cannot_append_extraction_records(tmp_path):
    ctx = make_context(tmp_path, FakeSandbox(), [])
    fabricated = json.loads(dispatch_tool(
        "record_result",
        {"candidate": "alpha", "doc_id": "doc-1", "ok": True,
         "prediction": {"invoice_number": "fabricated"}, "latency_s": 0.000001},
        ctx,
    ))

    assert fabricated == {"error": "unknown tool: record_result"}
    assert not (tmp_path / "results.jsonl").exists()
    assert "record_result" not in {
        schema["function"]["name"] for schema in __import__("engine.tools", fromlist=["TOOL_SCHEMAS"]).TOOL_SCHEMAS
    }


def test_auto_collation_enforces_scope_schema_size_and_deduplication(tmp_path):
    orchestrator = Orchestrator("collate", str(tmp_path), lambda _event, _data: None)
    orchestrator.ctx.allowed_candidate_names.add("alpha")
    orchestrator.ctx.allowed_doc_ids.add("doc-1")
    valid = {
        "ok": True,
        "doc_id": "doc-1",
        "fields": {
            "invoice_number": "INV-1",
            "date": "2026-01-01",
            "vendor": "Vendor",
            "total": "1.00",
        },
        "latency_s": 0.1,
    }

    orchestrator._collate("RESULT_JSON:" + json.dumps(valid), "alpha")
    with pytest.raises(ValueError, match="duplicate result"):
        orchestrator._collate("RESULT_JSON:" + json.dumps(valid), "alpha")
    with pytest.raises(ValueError, match="run capability"):
        orchestrator._collate(
            "RESULT_JSON:" + json.dumps({**valid, "doc_id": "unknown"}), "alpha"
        )
    with pytest.raises(ValueError, match="run capability"):
        orchestrator._collate("RESULT_JSON:" + json.dumps(valid), "unknown")
    with pytest.raises(ValueError, match="allowed size"):
        orchestrator._collate(
            "RESULT_JSON:"
            + json.dumps(
                {
                    **valid,
                    "doc_id": "doc-1",
                    "fields": {**valid["fields"], "vendor": "x" * 3000},
                }
            ),
            "alpha",
        )
    with pytest.raises(ValueError, match="invalid result payload"):
        orchestrator._collate("RESULT_JSON:not-json", "alpha")

    assert len((tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_evaluate_tool_is_bound_to_context_paths(tmp_path):
    ctx = make_context(tmp_path, FakeSandbox(), [])
    ground_truth = tmp_path / "ground_truth.csv"
    ground_truth.write_text(
        "doc_id,invoice_number,date,vendor,total\n"
        "doc-1,INV-1,2026-01-01,Vendor,1.00\n",
        encoding="utf-8",
    )
    record = {
        "candidate": "alpha",
        "doc_id": "doc-1",
        "ok": False,
        "prediction": None,
        "latency_s": 0,
        "error": "failed",
    }
    Path(ctx.results_path).write_text(json.dumps(record) + "\n", encoding="utf-8")
    ctx.allowed_dataset_root = str(tmp_path)
    ctx.ground_truth_path = str(ground_truth)
    other = tmp_path / "other.jsonl"
    other.write_text(json.dumps(record) + "\n", encoding="utf-8")

    rejected = json.loads(
        dispatch_tool(
            "evaluate",
            {"results_path": str(other), "ground_truth_path": str(ground_truth)},
            ctx,
        )
    )
    accepted = json.loads(
        dispatch_tool(
            "evaluate",
            {
                "results_path": ctx.results_path,
                "ground_truth_path": str(ground_truth),
            },
            ctx,
        )
    )

    assert "current run" in rejected["error"]
    assert accepted["alpha"]["failure_rate"] == 1.0
    assert ctx.evaluated_metrics == accepted


def test_evaluator_rejects_duplicate_candidate_document_records(tmp_path):
    ground_truth = tmp_path / "ground_truth.csv"
    ground_truth.write_text(
        "doc_id,invoice_number,date,vendor,total\n"
        "doc-1,INV-1,2026-01-01,Vendor,1.00\n",
        encoding="utf-8",
    )
    record = {
        "candidate": "alpha",
        "doc_id": "doc-1",
        "ok": False,
        "prediction": None,
        "latency_s": 0,
        "error": "failed",
    }
    results = tmp_path / "results.jsonl"
    results.write_text(
        json.dumps(record) + "\n" + json.dumps(record) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="duplicate result"):
        evaluate_results(str(results), str(ground_truth))


def test_evaluator_has_per_comparison_and_total_work_limits(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="work limit"):
        cer("x" * 2049, "y" * 2049)

    ground_truth = tmp_path / "ground_truth.csv"
    ground_truth.write_text(
        "doc_id,invoice_number,date,vendor,total\n"
        "doc-1,INVOICE,2026-01-01,Vendor,1.00\n",
        encoding="utf-8",
    )
    record = {
        "candidate": "alpha",
        "doc_id": "doc-1",
        "ok": True,
        "prediction": {
            "invoice_number": "DIFFERENT",
            "date": "2026-01-01",
            "vendor": "Vendor",
            "total": "1.00",
        },
        "latency_s": 0.1,
        "error": None,
    }
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(evaluate, "MAX_EVALUATION_CELLS", 10)

    with pytest.raises(ValueError, match="total work limit"):
        evaluate_results(str(results), str(ground_truth))


def test_rerun_clears_stale_results_and_accepts_same_key(tmp_path, monkeypatch):
    events: list = []
    orchestrator = Orchestrator("rerun", str(tmp_path), lambda e, d: events.append((e, d)))
    seen: list[bool] = []

    def implementation(_spec):
        seen.append(Path(orchestrator.results_path).exists())
        orchestrator.ctx.allowed_candidate_names.add("alpha")
        orchestrator.ctx.allowed_doc_ids.add("doc-1")
        append_result_record(
            orchestrator.ctx,
            {
                "candidate": "alpha",
                "doc_id": "doc-1",
                "ok": False,
                "prediction": None,
                "latency_s": 0.0,
                "error": "failed",
            },
        )
        return {"attempt": len(seen)}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    Path(orchestrator.results_path).write_text("stale\n", encoding="utf-8")
    assert orchestrator.run_tool_assessment({}) == {"attempt": 1}
    assert orchestrator.run_tool_assessment({}) == {"attempt": 2}

    assert seen == [False, False]
    assert len(Path(orchestrator.results_path).read_text(encoding="utf-8").splitlines()) == 1


def test_prepare_run_clears_attempt_state_and_preserves_registered_adapter(
    tmp_path, monkeypatch
):
    orchestrator = Orchestrator("fresh", str(tmp_path), lambda _event, _data: None)
    registered = Candidate(
        name="offline",
        display_name="Offline",
        docs_url="",
        kind="local_tool",
        build_commands=[],
        adapter_code="def extract(_path): return {}",
    )
    stale = Candidate(
        name="stale",
        display_name="Stale",
        docs_url="",
        kind="local_tool",
        build_commands=[],
        adapter_code="def extract(_path): return {}",
    )
    orchestrator.register_candidate(registered)
    observations = []

    def implementation(_spec):
        observations.append(
            (
                set(orchestrator.ctx.candidates),
                list(orchestrator.ctx.citations),
                (tmp_path / "pricing.json").exists(),
            )
        )
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    spec = {"candidates": [{"name": "offline"}]}
    orchestrator.run_tool_assessment(spec)
    orchestrator.ctx.candidates["stale"] = stale
    orchestrator.ctx.citations.append({"title": "stale", "url": "stale"})
    (tmp_path / "pricing.json").write_text('{"stale": 1}', encoding="utf-8")
    orchestrator.run_tool_assessment(spec)

    assert observations == [({"offline"}, [], False), ({"offline"}, [], False)]


def test_sandbox_entitlements_are_explicit_not_candidate_label_derived(
    tmp_path, monkeypatch
):
    provider_env = {
        "DEEPSEEK_API_KEY": "deep-secret",
        "OPENAI_API_KEY": "open-secret",
        "NOSANA_API_KEY": "nosana-secret",
    }
    orchestrator = Orchestrator(
        "entitlements", str(tmp_path), lambda _event, _data: None, provider_env=provider_env
    )

    def implementation(_spec):
        deep = orchestrator._adapter_code(
            Candidate("DEEPSEEK", "Deep", "", "hosted_api", [],
                      'import os\nprint(os.environ["DEEPSEEK_API_KEY"])'),
            "image.png",
        )
        openai = orchestrator._adapter_code(
            Candidate("OPENAI", "OpenAI", "", "hosted_api", [],
                      'import os\nprint(os.environ["OPENAI_API_KEY"])'),
            "image.png",
        )
        nosana = orchestrator._adapter_code(
            Candidate("nosana_vlm", "Nosana", "", "hosted_api", [],
                      'import os\nprint(os.environ["NOSANA_API_KEY"])'),
            "image.png",
        )
        assert "deep-secret" not in deep
        assert "open-secret" not in openai
        assert "nosana-secret" not in nosana
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    orchestrator.run_tool_assessment(
        {"candidates": [{"name": "DEEPSEEK"}, {"name": "OPENAI"}, {"name": "nosana_vlm"}]}
    )

    trusted = Candidate(
        "trusted-nosana", "Trusted Nosana", "", "hosted_api", [],
        'import os\nprint(os.environ["NOSANA_API_KEY"])',
    )
    token = orchestrator.register_trusted_candidate(trusted, ["NOSANA_API_KEY"])

    def name_only_impl(_spec):
        assert "trusted-nosana" not in orchestrator.ctx.candidates
        code = orchestrator._adapter_code(trusted, "image.png")
        assert "nosana-secret" not in code
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", name_only_impl)
    orchestrator.run_tool_assessment({"candidates": [{"name": "trusted-nosana"}]})

    def entitled_impl(_spec):
        adapter = orchestrator.ctx.candidates["trusted-nosana"]
        code = orchestrator._adapter_code(adapter, "image.png")
        assert "nosana-secret" in code
        assert "open-secret" not in code
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", entitled_impl)
    orchestrator.run_tool_assessment(
        {"candidates": [{"name": "trusted-nosana", "trusted_adapter_token": token}]}
    )
    with pytest.raises(ValueError, match="invalid trusted adapter capability"):
        orchestrator.run_tool_assessment(
            {
                "candidates": [
                    {"name": "trusted-nosana", "trusted_adapter_token": token}
                ]
            }
        )

    # Orchestration credentials that no first-party adapter needs stay
    # permanently un-entitleable, whatever the candidate claims to be.
    for forbidden in ("DAYTONA_API_KEY", "DEEPSEEK_API_KEY", "OXYLABS_PASSWORD"):
        orchestrator.ctx.env_passthrough[forbidden] = "orchestration-secret"
        with pytest.raises(ValueError, match="orchestration credentials"):
            orchestrator.register_trusted_candidate(
                Candidate("forbidden", "Forbidden", "", "hosted_api", [], "pass"),
                [forbidden],
            )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://localhost/path",
        "https://127.0.0.1/path",
        "https://[::1]/path",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://user:password@example.com/path",
    ],
)
def test_external_url_policy_rejects_unsafe_targets_without_echo(url):
    with pytest.raises(ValueError, match="external URL is not permitted") as raised:
        validate_external_url(url, resolver=lambda *_args, **_kwargs: [])
    assert url not in str(raised.value)


def test_external_url_policy_rejects_hostname_resolving_private_address():
    def private_resolver(_host, port, **_kwargs):
        return [(2, 1, 6, "", ("10.0.0.8", port))]

    with pytest.raises(ValueError, match="external URL is not permitted"):
        validate_external_url("https://service.example/path", resolver=private_resolver)


def test_outbound_policy_revalidates_dns_before_each_transport_request():
    answers = iter(["93.184.216.34", "10.0.0.8"])

    def rebinding_resolver(_host, port, **_kwargs):
        return [(2, 1, 6, "", (next(answers), port))]

    policy = OutboundURLPolicy({"service.example"}, resolver=rebinding_resolver)
    assert policy.validate("https://service.example/v1")
    with pytest.raises(ValueError, match="external URL is not permitted"):
        policy.request_hook(SimpleNamespace(url="https://service.example/v1/chat"))


def test_outbound_policy_rejects_unregistered_public_hostname():
    with pytest.raises(ValueError, match="external URL is not permitted"):
        validate_external_url(
            "https://attacker.example/v1",
            allowed_hosts={"api.deepseek.com"},
            resolver=lambda _host, port, **_kwargs: [
                (2, 1, 6, "", ("93.184.216.34", port))
            ],
        )


def test_secure_http_transport_does_not_follow_redirects():
    import httpx

    calls = []

    def redirecting_transport(request):
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    _base, client = secure_httpx_client(
        "https://8.8.8.8/v1", {"8.8.8.8"}
    )
    client._transport = httpx.MockTransport(redirecting_transport)
    try:
        response = client.get("https://8.8.8.8/v1/models")
    finally:
        client.close()

    assert response.status_code == 302
    assert calls == ["https://8.8.8.8/v1/models"]


def test_configurable_transport_locks_initial_hostname():
    import httpx

    _base, client = secure_httpx_client("https://8.8.8.8/v1")
    client._transport = httpx.MockTransport(
        lambda request: httpx.Response(200, request=request)
    )
    try:
        with pytest.raises(ValueError, match="external URL is not permitted"):
            client.get("https://1.1.1.1/v1/models")
    finally:
        client.close()


def test_secure_http_transports_ignore_process_proxy_configuration():
    _base, sync_client = secure_httpx_client(
        "https://8.8.8.8/v1", {"8.8.8.8"}
    )
    _base, async_client = secure_async_httpx_client(
        "https://8.8.8.8/v1", {"8.8.8.8"}
    )
    try:
        assert sync_client.follow_redirects is False
        assert sync_client._trust_env is False
        assert async_client.follow_redirects is False
        assert async_client._trust_env is False
    finally:
        sync_client.close()
        import asyncio

        asyncio.run(async_client.aclose())


def test_socket_backend_pins_validated_ip_and_rejects_connection_time_rebinding():
    from engine.network_security import _PinnedSyncBackend

    answers = iter(["93.184.216.34", "10.0.0.8"])

    def resolver(_host, port, **_kwargs):
        return [(2, 1, 6, "", (next(answers), port))]

    policy = OutboundURLPolicy({"service.example"}, resolver=resolver)
    assert policy.validate("https://service.example/v1")
    backend = _PinnedSyncBackend(policy)
    backend.backend = SimpleNamespace(connect_tcp=lambda *_args, **_kwargs: object())
    with pytest.raises(ValueError, match="external URL is not permitted"):
        backend.connect_tcp("service.example", 443)


def test_socket_backend_connects_to_approved_ip_not_hostname():
    from engine.network_security import _PinnedSyncBackend

    policy = OutboundURLPolicy(
        {"service.example"},
        resolver=lambda _host, port, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", port))
        ],
    )
    observed = []
    backend = _PinnedSyncBackend(policy)
    sentinel = object()
    backend.backend = SimpleNamespace(
        connect_tcp=lambda host, *_args, **_kwargs: observed.append(host) or sentinel
    )
    assert backend.connect_tcp("service.example", 443) is sentinel
    assert observed == ["93.184.216.34"]


def test_engine_enumerates_every_api_accepted_image_format(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset"
    images = dataset / "images"
    images.mkdir(parents=True)
    (dataset / "ground_truth.csv").write_text("doc_id,value\ninvoice,1\n", encoding="utf-8")
    for filename in ("a.png", "b.jpg", "c.jpeg", "d.webp", "ignored.txt"):
        (images / filename).write_bytes(b"fixture")
    monkeypatch.setenv("PROOFBENCH_DATASET_ROOT", str(tmp_path))
    orchestrator = Orchestrator("formats", str(tmp_path / "run"), lambda *_args: None)
    spec = {"dataset": {"path": str(dataset)}}
    orchestrator._prepare_run(spec)
    assert orchestrator.ctx.allowed_doc_ids == {"a", "b", "c", "d"}
    assert orchestrator._list_images(str(dataset)) == ["a.png", "b.jpg", "c.jpeg", "d.webp"]


@pytest.mark.parametrize(
    ("env", "expected_base", "expected_host"),
    [
        (
            {"ORCHESTRATOR_PROVIDER": "moonshot", "MOONSHOT_API_KEY": "key"},
            "https://api.moonshot.ai/v1",
            "api.moonshot.ai",
        ),
        (
            {"ORCHESTRATOR_PROVIDER": "openai", "OPENAI_API_KEY": "key"},
            "https://api.openai.com/v1",
            "api.openai.com",
        ),
        # OpenRouter alone, with no OpenAI/Moonshot key present.
        (
            {"OPENROUTER_API_KEY": "key"},
            "https://openrouter.ai/api/v1",
            None,
        ),
    ],
)
def test_orchestrator_clients_install_secure_transport(
    monkeypatch, env, expected_base, expected_host
):
    from engine import agent, network_security

    captured = {}
    sentinel = object()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

    def fake_secure(base_url, allowed_hosts=None):
        captured["transport"] = (base_url, allowed_hosts)
        return base_url, sentinel

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(network_security, "secure_httpx_client", fake_secure)

    agent._orchestrator_client(env)

    # A server-owned constant host is pinned by name; a deployment-configurable
    # base URL passes None, which secure_httpx_client locks to its own hostname.
    expected_hosts = None if expected_host is None else {expected_host}
    assert captured["transport"] == (expected_base, expected_hosts)
    assert captured["client_kwargs"]["http_client"] is sentinel
    assert captured["client_kwargs"]["max_retries"] == 0


@pytest.mark.parametrize(
    ("env", "expected_base", "expected_host"),
    [
        (
            {"ORCHESTRATOR_PROVIDER": "moonshot", "MOONSHOT_API_KEY": "key"},
            "https://api.moonshot.ai/v1",
            "api.moonshot.ai",
        ),
        (
            {"ORCHESTRATOR_PROVIDER": "openai", "OPENAI_API_KEY": "key"},
            "https://api.openai.com/v1",
            "api.openai.com",
        ),
        # OpenRouter alone, with no OpenAI/Moonshot key present.
        (
            {"OPENROUTER_API_KEY": "key"},
            "https://openrouter.ai/api/v1",
            None,
        ),
    ],
)
def test_report_clients_install_secure_transport(
    tmp_path, monkeypatch, env, expected_base, expected_host
):
    from engine import network_security, report_gen

    captured = {}
    sentinel = object()

    class FakeCompletions:
        @staticmethod
        def create(**_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="# report"))]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    def fake_secure(base_url, allowed_hosts=None):
        captured["transport"] = (base_url, allowed_hosts)
        return base_url, sentinel

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(network_security, "secure_httpx_client", fake_secure)

    report_gen.write_report({}, [], str(tmp_path / "report.md"), env=env)

    # A server-owned constant host is pinned by name; a deployment-configurable
    # base URL passes None, which secure_httpx_client locks to its own hostname.
    expected_hosts = None if expected_host is None else {expected_host}
    assert captured["transport"] == (expected_base, expected_hosts)
    assert captured["client_kwargs"]["http_client"] is sentinel
    assert captured["client_kwargs"]["max_retries"] == 0


def test_deepseek_client_installs_secure_transport(monkeypatch):
    from engine import network_security
    from engine.llm_clients import deepseek_client

    captured = {}
    sentinel = object()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

    def fake_secure(base_url, allowed_hosts=None):
        captured["transport"] = (base_url, allowed_hosts)
        return base_url, sentinel

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(network_security, "secure_httpx_client", fake_secure)

    deepseek_client({"DEEPSEEK_API_KEY": "key"})

    assert captured["transport"] == (
        "https://api.deepseek.com",
        None,
    )
    assert captured["client_kwargs"]["http_client"] is sentinel
    assert captured["client_kwargs"]["max_retries"] == 0


def test_doubleword_primary_and_polling_clients_install_secure_transport(monkeypatch):
    from engine import network_security
    from engine.llm_clients import doubleword_batch_client

    captured = {"secure_calls": []}
    primary_transport = object()
    polling_transport = object()
    transports = iter([primary_transport, polling_transport])

    class FakeBatchOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self._http_client = object()

    def fake_secure(base_url, allowed_hosts=None):
        captured["secure_calls"].append((base_url, allowed_hosts))
        return base_url, next(transports)

    monkeypatch.setitem(
        sys.modules,
        "autobatcher",
        SimpleNamespace(BatchOpenAI=FakeBatchOpenAI),
    )
    monkeypatch.setattr(network_security, "secure_async_httpx_client", fake_secure)

    client = doubleword_batch_client({"DOUBLEWORD_API_KEY": "key"})

    assert captured["secure_calls"] == [
        ("https://api.doubleword.ai/v1", None),
        ("https://api.doubleword.ai/v1", None),
    ]
    assert captured["client_kwargs"]["http_client"] is primary_transport
    assert captured["client_kwargs"]["max_retries"] == 0
    assert client._http_client is polling_transport


def test_embedded_sandbox_transport_revalidates_rebound_provider_dns(monkeypatch):
    from engine.candidates.fallbacks._http_security import SECURE_OPENAI_TRANSPORT

    namespace = {}
    exec(SECURE_OPENAI_TRANSPORT, namespace)
    answers = iter(["93.184.216.34", "10.0.0.8"])

    def rebinding_resolver(_host, port, **_kwargs):
        return [(2, 1, 6, "", (next(answers), port))]

    monkeypatch.setattr(namespace["socket"], "getaddrinfo", rebinding_resolver)
    _base, client = namespace["_secure_openai_transport"](
        "https://provider.example/v1", {"provider.example"}
    )
    try:
        with pytest.raises(ValueError, match="provider URL is not permitted"):
            client.get("https://provider.example/v1/models")
    finally:
        client.close()


def test_embedded_sandbox_transport_pins_connection_to_validated_ip(monkeypatch):
    from engine.candidates.fallbacks._http_security import SECURE_OPENAI_TRANSPORT

    namespace = {}
    exec(SECURE_OPENAI_TRANSPORT, namespace)
    monkeypatch.setattr(
        namespace["socket"], "getaddrinfo",
        lambda _host, port, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    _base, client = namespace["_secure_openai_transport"](
        "https://provider.example/v1", {"provider.example"}
    )
    try:
        pinned = client._transport._pool._network_backend
        observed = []
        sentinel = object()
        pinned.backend = SimpleNamespace(
            connect_tcp=lambda host, *_args, **_kwargs: observed.append(host) or sentinel
        )
        assert pinned.connect_tcp("provider.example", 443) is sentinel
        assert observed == ["93.184.216.34"]
    finally:
        client.close()


@pytest.mark.parametrize("candidate_name", ["doubleword", "nosana_vlm", "openai_vision"])
def test_hosted_fallback_adapters_disable_redirects_proxies_and_retries(candidate_name):
    module = importlib.import_module(f"engine.candidates.fallbacks.{candidate_name}")
    adapter = module.candidate().adapter_code

    assert "follow_redirects=False" in adapter
    assert "trust_env=False" in adapter
    assert "event_hooks={\"request\": [request_hook]}" in adapter
    assert "max_retries=0" in adapter


@pytest.mark.parametrize("candidate_name", ["doubleword", "nosana_vlm", "openai_vision"])
def test_hosted_fallback_adapters_label_supported_image_media_types(candidate_name):
    module = importlib.import_module(f"engine.candidates.fallbacks.{candidate_name}")
    adapter = module.candidate().adapter_code

    assert '".jpg": "image/jpeg"' in adapter
    assert '".jpeg": "image/jpeg"' in adapter
    assert '".png": "image/png"' in adapter
    assert '".webp": "image/webp"' in adapter
    assert "unsupported image format" in adapter


def test_oxylabs_transport_refuses_redirects_and_process_proxies(monkeypatch):
    captured = {}

    class RedirectResponse:
        status_code = 302

    class FakeClient:
        def post(self, _url, **kwargs):
            captured["kwargs"] = kwargs
            return RedirectResponse()

        def close(self):
            captured["closed"] = True

    from engine import network_security

    monkeypatch.setattr(
        network_security, "secure_httpx_client",
        lambda url, allowed_hosts=None: (url, FakeClient()),
    )

    with pytest.raises(RuntimeError, match="HTTP 302") as raised:
        docs_intel._query(
            {"source": "google_search", "query": "docs"},
            {"OXYLABS_USERNAME": "user", "OXYLABS_PASSWORD": "password"},
        )

    assert captured["closed"] is True
    assert "allow_redirects" not in captured["kwargs"]
    assert "169.254.169.254" not in str(raised.value)


def test_clients_and_tools_use_immutable_orchestrator_runtime_snapshot(
    tmp_path, monkeypatch
):
    from engine import adapter_gen, report_gen
    from engine.llm_clients import deepseek_client

    monkeypatch.setenv("SNAPSHOT_MARKER", "process-before")
    orchestrator = Orchestrator(
        "runtime-snapshot",
        str(tmp_path),
        lambda _event, _data: None,
        provider_env={"SNAPSHOT_MARKER": "run-owned"},
    )
    orchestrator.ctx.allowed_candidate_names.add("alpha")
    monkeypatch.setenv("SNAPSHOT_MARKER", "process-after")
    observed = []

    monkeypatch.setattr(
        docs_intel,
        "web_search",
        lambda _query, env=None: observed.append(env["SNAPSHOT_MARKER"]) or [],
    )
    monkeypatch.setattr(
        adapter_gen,
        "generate_adapter",
        lambda name, _docs, env=None: observed.append(env["SNAPSHOT_MARKER"])
        or Candidate(name, name, "", "local_tool", [], "pass"),
    )
    monkeypatch.setattr(
        report_gen,
        "write_report",
        lambda _metrics, _citations, _path, env=None: observed.append(
            env["SNAPSHOT_MARKER"]
        )
        or "report",
    )

    dispatch_tool("web_search", {"query": "docs"}, orchestrator.ctx)
    dispatch_tool(
        "generate_adapter", {"tool_name": "alpha", "docs_md": "docs"}, orchestrator.ctx
    )
    orchestrator.ctx.evaluated_metrics = {"alpha": {}}
    dispatch_tool("write_report", {"metrics_json": "{}"}, orchestrator.ctx)

    assert observed == ["run-owned", "run-owned", "run-owned"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-only-secret")
    with pytest.raises(RuntimeError, match="required"):
        deepseek_client({})
    with pytest.raises(ValueError, match="external URL is not permitted"):
        deepseek_client(
            {
                "DEEPSEEK_API_KEY": "run-key",
                "DEEPSEEK_BASE_URL": "https://127.0.0.1/v1",
            }
        )


def test_assessment_redacts_files_pdf_citations_and_events(tmp_path, monkeypatch):
    secret = "assessment-secret-value"
    events = []
    orchestrator = Orchestrator(
        "assessment-redaction",
        str(tmp_path),
        lambda event, data: events.append((event, data)),
        provider_env={"ACME_API_KEY": secret},
    )
    monkeypatch.setattr(
        docs_intel, "scrape_page", lambda _url, env=None: f"docs {secret}"
    )
    monkeypatch.setattr(
        tool_assessment,
        "assess_documentation_batch",
        lambda candidates, objective, env=None: {
            "acme": {"error": f"provider failed with {secret}"}
        },
    )

    def fake_report(metrics, citations, out_path):
        text = json.dumps({"metrics": metrics, "citations": citations})
        Path(out_path).write_text(text, encoding="utf-8")
        return text

    monkeypatch.setattr(tool_assessment, "write_assessment_report", fake_report)
    monkeypatch.setattr(
        pdf_report,
        "write_pdf_report",
        lambda metrics, report, out_path: Path(out_path).write_text(
            json.dumps(metrics) + report, encoding="utf-8"
        ),
    )
    metrics = orchestrator.run_tool_assessment(
        {
            "candidates": [
                {
                    "name": "acme",
                    "display_name": "Acme",
                    "docs_url": f"https://example.test/docs?key={secret}",
                }
            ]
        }
    )

    assert secret not in json.dumps(metrics)
    persisted = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert persisted == {"provenance": "measured", "metrics": metrics}
    for name in ("metrics.json", "report.md", "report.pdf"):
        assert secret not in (tmp_path / name).read_text(encoding="utf-8")
    assert secret not in json.dumps(events)
    assert all(
        data.get("provenance") == "measured"
        for event, data in events
        if event == "artifact" and data.get("kind") in {"results", "report"}
    )


def test_pdf_failure_preserves_measured_assessment_metrics_and_marks_report_missing(
    tmp_path, monkeypatch
):
    events = []
    orchestrator = Orchestrator("pdf-warning", str(tmp_path),
                                lambda event, data: events.append((event, data)))
    monkeypatch.setattr(docs_intel, "scrape_page", lambda _url, env=None: "official docs")
    monkeypatch.setattr(tool_assessment, "assess_documentation_batch", lambda *_args, **_kwargs: {
        "alpha": {"error": "assessment unavailable"}
    })
    monkeypatch.setattr(tool_assessment, "write_assessment_report",
                        lambda *_args, **_kwargs: "# measured assessment")
    monkeypatch.setattr(pdf_report, "write_pdf_report",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("renderer unavailable")))

    metrics = orchestrator.run_tool_assessment({"candidates": [{
        "name": "alpha", "display_name": "Alpha", "docs_url": "https://example.com/docs",
    }]})

    assert json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8")) == {
        "provenance": "measured", "metrics": metrics}
    assert not (tmp_path / "report.pdf").exists()
    warning = next(data for event, data in events
                   if event == "artifact" and data.get("kind") == "report" and not data.get("available", True))
    assert warning["provenance"] == "measured"
    assert orchestrator.artifact_warnings


def test_engine_exposes_no_simulated_run_path():
    """The demo generator is gone; nothing in the engine can synthesise metrics."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("engine.demo_fallback")

    engine_dir = Path(__file__).resolve().parent
    sources = [
        path for path in engine_dir.rglob("*.py")
        if not path.name.startswith("test_")
    ]
    assert sources
    offenders = [
        str(path.relative_to(engine_dir))
        for path in sources
        if "demo_fallback" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize("cancelled", [False, True])
def test_run_cleanup_destroys_remote_resource_on_failure_or_cancellation(
    tmp_path, monkeypatch, cancelled
):
    events: list = []
    cancel_event = threading.Event()
    if cancelled:
        cancel_event.set()
    orchestrator = Orchestrator(
        "cleanup",
        str(tmp_path),
        lambda event, data: events.append((event, data)),
        cancel_event=cancel_event,
    )
    sandbox = FakeSandbox()
    monkeypatch.setattr(orchestrator.pool, "_create_one", lambda: sandbox)

    def implementation(_spec):
        handle = orchestrator.pool.acquire("alpha")
        orchestrator.ctx.sandbox_handles[handle.id] = handle
        if cancelled:
            orchestrator._check_cancelled()
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    with pytest.raises(RuntimeError):
        orchestrator.run_tool_assessment({})

    assert sandbox.deleted == 1
    assert orchestrator.ctx.sandbox_handles == {}
    assert any(event == "error" for event, _ in events)


def test_run_cleanup_destroys_remote_resource_on_success(tmp_path, monkeypatch):
    orchestrator = Orchestrator("success", str(tmp_path), lambda _event, _data: None)
    sandbox = FakeSandbox()
    monkeypatch.setattr(orchestrator.pool, "_create_one", lambda: sandbox)

    def implementation(_spec):
        handle = orchestrator.pool.acquire("alpha")
        orchestrator.ctx.sandbox_handles[handle.id] = handle
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    assert orchestrator.run_tool_assessment({}) == {"ok": True}
    assert sandbox.deleted == 1
    assert orchestrator.ctx.sandbox_handles == {}


def test_real_run_without_results_fails_instead_of_using_demo_metrics(tmp_path, monkeypatch):
    events: list = []
    orchestrator = Orchestrator(
        "real-empty",
        str(tmp_path),
        lambda event, data: events.append((event, data)),
        provider_env={"PROOFBENCH_DATASET_ROOT": str(tmp_path)},
    )
    ground_truth = tmp_path / "ground_truth.csv"
    ground_truth.write_text(
        "doc_id,invoice_number,date,vendor,total\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        orchestrator,
        "_run_tool_assessment_impl",
        lambda _spec: orchestrator._evaluate_and_report(str(ground_truth)),
    )

    with pytest.raises(RuntimeError, match="no valid result records"):
        orchestrator.run_tool_assessment(
            {"candidates": [], "dataset": {"path": str(tmp_path)}}
        )

    assert not (tmp_path / "metrics.json").exists()
    assert any(
        event == "error" and data["message"] == "RuntimeError: benchmark execution failed"
        for event, data in events
    )


# --------------------------------------------------- dataset root confinement
def _labelled_dataset(path: Path) -> Path:
    """A minimally valid dataset: ground truth plus one image."""
    (path / "images").mkdir(parents=True)
    (path / "images" / "inv_001.png").write_bytes(b"fixture")
    (path / "ground_truth.csv").write_text(
        "doc_id,invoice_number,date,vendor,total\ninv_001,1,2024-01-01,acme,1.00\n",
        encoding="utf-8",
    )
    return path


def _confinement_orchestrator(tmp_path, monkeypatch, upload_root, sample):
    """An orchestrator whose upload root and sample dataset are both redirected."""
    from engine import agent

    monkeypatch.setenv("PROOFBENCH_DATASET_ROOT", str(upload_root))
    monkeypatch.setattr(agent, "SAMPLE_DATASET_PATH", sample)
    return Orchestrator("confine", str(tmp_path / "run"), lambda *_args: None)


def test_sample_dataset_is_accepted_though_it_sits_outside_the_upload_root(
    tmp_path, monkeypatch
):
    # The deployed shape: PROOFBENCH_DATASET_ROOT is the tenant upload root and
    # the server-owned sample dataset is its sibling, not its descendant.
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)

    spec = {"dataset": {"path": str(sample)}}
    orchestrator._prepare_run(spec)

    assert orchestrator.ctx.allowed_dataset_root == str(sample.resolve())
    assert orchestrator.ctx.ground_truth_path == str(
        (sample / "ground_truth.csv").resolve()
    )
    assert orchestrator.ctx.allowed_doc_ids == {"inv_001"}


def test_sibling_of_the_sample_dataset_is_still_rejected(tmp_path, monkeypatch):
    # Naming the sample path must not open up everything beside it.
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")
    sibling = _labelled_dataset(data / "not_demo")
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)

    with pytest.raises(ValueError, match="within the configured dataset root"):
        orchestrator._prepare_run({"dataset": {"path": str(sibling)}})


def test_dataset_fully_outside_both_roots_is_rejected(tmp_path, monkeypatch):
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")
    outside = _labelled_dataset(tmp_path / "elsewhere")
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)

    with pytest.raises(ValueError, match="within the configured dataset root"):
        orchestrator._prepare_run({"dataset": {"path": str(outside)}})


def test_upload_subtree_dataset_is_still_accepted(tmp_path, monkeypatch):
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")
    uploaded = _labelled_dataset(uploads / "a1b2c3d4e5f6")
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)

    orchestrator._prepare_run({"dataset": {"path": str(uploaded)}})

    assert orchestrator.ctx.allowed_dataset_root == str(uploaded.resolve())


def test_symlink_out_of_the_upload_root_is_rejected(tmp_path, monkeypatch):
    # Confinement is judged on the resolved target, so a link parked inside the
    # upload root cannot smuggle in a directory from outside it.
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")
    outside = _labelled_dataset(tmp_path / "elsewhere")
    link = uploads / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted in this environment")
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)

    with pytest.raises(ValueError, match="within the configured dataset root"):
        orchestrator._prepare_run({"dataset": {"path": str(link)}})


def test_a_file_at_the_sample_path_is_rejected_as_not_a_directory(tmp_path, monkeypatch):
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = data / "demo"
    sample.write_text("not a dataset", encoding="utf-8")
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)

    with pytest.raises(ValueError, match="must be a directory"):
        orchestrator._prepare_run({"dataset": {"path": str(sample)}})


def test_sample_dataset_path_is_derived_from_the_application_root():
    # The canonical sample dataset the server owns is .../data/demo beside the
    # engine package, matching what server.storage registers as synthetic.
    from engine import agent

    assert agent.SAMPLE_DATASET_PATH == agent.APP_ROOT / "data" / "demo"
    assert (agent.APP_ROOT / "engine" / "agent.py").is_file()


def test_relative_dataset_root_is_anchored_to_the_application_root():
    # server.storage anchors a relative root to the same application root; if
    # the engine resolved it against the process CWD the two would disagree
    # about what counts as confined.
    from engine import agent

    upload_root, _sample = agent._dataset_roots({"PROOFBENCH_DATASET_ROOT": "data/uploads"})

    assert upload_root == (agent.APP_ROOT / "data" / "uploads").resolve()


def test_unset_dataset_root_falls_back_to_the_server_upload_directory():
    # server.storage defaults PROOFBENCH_DATASET_ROOT to data/uploads; the
    # engine has to agree, or an unset deployment would confine uploads to a
    # wider root than the server writes them into.
    from engine import agent

    upload_root, _sample = agent._dataset_roots({})

    assert upload_root == (agent.APP_ROOT / "data" / "uploads").resolve()


# ------------------------------------------------- dataset child confinement
def _skip_without_symlinks(make_link) -> None:
    """Run a symlink factory, skipping only when the OS genuinely refuses."""
    try:
        make_link()
    except (OSError, NotImplementedError) as exc:  # e.g. unprivileged Windows
        pytest.skip(f"symlink creation is not permitted here: {type(exc).__name__}")


def test_symlinked_images_directory_cannot_escape_the_dataset(tmp_path, monkeypatch):
    # The dataset itself is confined, but its images/ entry is a link to a
    # directory outside it. Following that would expose arbitrary host files.
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")

    outside_images = tmp_path / "secrets"
    outside_images.mkdir()
    (outside_images / "inv_001.png").write_bytes(b"secret")

    dataset = uploads / "a1b2c3d4e5f6"
    dataset.mkdir()
    (dataset / "ground_truth.csv").write_text(
        "doc_id,invoice_number,date,vendor,total\ninv_001,1,2024-01-01,acme,1.00\n",
        encoding="utf-8",
    )
    _skip_without_symlinks(
        lambda: (dataset / "images").symlink_to(outside_images, target_is_directory=True)
    )
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)

    with pytest.raises(ValueError) as excinfo:
        orchestrator._prepare_run({"dataset": {"path": str(dataset)}})

    assert "outside the dataset root" in str(excinfo.value)
    assert str(outside_images) not in str(excinfo.value)
    assert orchestrator.ctx.allowed_doc_ids == set()


def test_symlinked_image_file_cannot_escape_the_dataset(tmp_path, monkeypatch):
    # The images directory is real, but one entry inside it links out.
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    secret = outside / "id_rsa.png"
    secret.write_bytes(b"secret")

    dataset = _labelled_dataset(uploads / "a1b2c3d4e5f6")
    _skip_without_symlinks(lambda: (dataset / "images" / "leak.png").symlink_to(secret))
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)

    with pytest.raises(ValueError) as excinfo:
        orchestrator._prepare_run({"dataset": {"path": str(dataset)}})

    assert "outside the dataset root" in str(excinfo.value)
    assert str(secret) not in str(excinfo.value)


def test_upload_rejects_an_images_symlink_planted_after_preparation(tmp_path, monkeypatch):
    # Preparation passed on a clean dataset; the escape is introduced before the
    # upload. Re-resolving at the point of use is what catches it.
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "id_rsa.png").write_bytes(b"secret")

    dataset = _labelled_dataset(uploads / "a1b2c3d4e5f6")
    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)
    orchestrator._prepare_run({"dataset": {"path": str(dataset)}})
    assert orchestrator.ctx.allowed_doc_ids == {"inv_001"}

    shutil.rmtree(dataset / "images")
    _skip_without_symlinks(
        lambda: (dataset / "images").symlink_to(outside, target_is_directory=True)
    )
    orchestrator._dataset_path = str(dataset.resolve())
    uploaded: list = []
    orchestrator.pool = SimpleNamespace(
        upload=lambda *args: uploaded.append(args)
    )

    with pytest.raises(ValueError, match="outside the dataset root"):
        orchestrator._upload_dataset(SimpleNamespace(id="h", label="cand"))

    assert uploaded == []


def test_upload_sends_only_confined_dataset_files(tmp_path, monkeypatch):
    # The normal path still works: every real image plus ground truth, uploaded
    # from its resolved location under the prepared dataset root.
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")
    dataset = _labelled_dataset(uploads / "a1b2c3d4e5f6")
    (dataset / "images" / "inv_002.jpg").write_bytes(b"fixture")

    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)
    orchestrator._prepare_run({"dataset": {"path": str(dataset)}})
    assert orchestrator.ctx.allowed_doc_ids == {"inv_001", "inv_002"}

    orchestrator._dataset_path = str(dataset.resolve())
    uploaded: list = []
    orchestrator.pool = SimpleNamespace(
        upload=lambda _handle, source, target: uploaded.append((source, target))
    )
    orchestrator._upload_dataset(SimpleNamespace(id="h", label="cand"))

    images = dataset.resolve() / "images"
    assert uploaded == [
        (str(images / "inv_001.png"), "images/inv_001.png"),
        (str(images / "inv_002.jpg"), "images/inv_002.jpg"),
        (str((dataset / "ground_truth.csv").resolve()), "ground_truth.csv"),
    ]


def test_upload_refuses_a_dataset_other_than_the_prepared_root(tmp_path, monkeypatch):
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    sample = _labelled_dataset(data / "demo")
    dataset = _labelled_dataset(uploads / "a1b2c3d4e5f6")
    other = _labelled_dataset(uploads / "b2c3d4e5f6a1")

    orchestrator = _confinement_orchestrator(tmp_path, monkeypatch, uploads, sample)
    orchestrator._prepare_run({"dataset": {"path": str(dataset)}})
    orchestrator._dataset_path = str(other.resolve())
    orchestrator.pool = SimpleNamespace(upload=lambda *args: None)

    with pytest.raises(ValueError, match="not the prepared dataset root"):
        orchestrator._upload_dataset(SimpleNamespace(id="h", label="cand"))


# ------------------------------------------- agent tool loop phase reporting
class _ToolCallingClient:
    """One tool call, then DONE — the shape of a real orchestrator turn."""

    def __init__(self, tool_name: str, args: dict):
        self._turns = [
            SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        id="call-1",
                        function=SimpleNamespace(
                            name=tool_name, arguments=json.dumps(args)
                        ),
                    )
                ],
                model_dump=lambda **_kwargs: {"role": "assistant", "content": ""},
            ),
            SimpleNamespace(content="DONE", tool_calls=None),
        ]
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        message = self._turns.pop(0) if self._turns else self._turns_exhausted()
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    @staticmethod
    def _turns_exhausted():
        raise AssertionError("the agent loop asked for more turns than expected")


def _tool_loop_orchestrator(tmp_path, monkeypatch, events, tool_name, args):
    from engine import agent

    dataset = _labelled_dataset(tmp_path / "dataset")
    orchestrator = Orchestrator(
        "toolloop",
        str(tmp_path / "run"),
        lambda event, data: events.append((event, data)),
        provider_env={"PROOFBENCH_DATASET_ROOT": str(tmp_path)},
    )
    monkeypatch.setattr(
        agent, "_orchestrator_client", lambda _env: _ToolCallingClient(tool_name, args)
    )
    monkeypatch.setattr(agent, "_orchestrator_model", lambda _env: "test-model")
    # The tool itself is not under test here; the network must stay untouched.
    monkeypatch.setattr(orchestrator, "_dispatch_with_collation", lambda *_a: "{}")
    return orchestrator, dataset


def test_extraction_run_bypasses_model_tool_calls_and_uses_controlled_pipeline(
    tmp_path, monkeypatch
):
    """A fabricating orchestrator cannot author runners or result records."""
    events: list = []
    orchestrator, dataset = _tool_loop_orchestrator(
        tmp_path, monkeypatch, events, "scrape_docs", {"url": "https://example.com"}
    )
    scripted: list = []
    monkeypatch.setattr(orchestrator, "run_benchmark_scripted",
                        lambda spec: scripted.append(spec) or {"ok": {}})

    spec = {
        "benchmark_type": "extraction",
        "candidates": [{"name": "tesseract"}, {"name": "easyocr"}],
        "dataset": {"path": str(dataset)},
    }
    assert orchestrator.run_benchmark(spec) == {"ok": {}}

    assert scripted == [spec]
    # The model client was deliberately wired to attempt a tool call, but no
    # provider call or model-authored placeholder runner reached execution.
    assert not events


def test_scripted_extraction_generates_non_builtin_adapter_once_before_execution(
    tmp_path, monkeypatch
):
    from engine import adapter_gen

    orchestrator = Orchestrator("generated", str(tmp_path), lambda *_args: None)
    calls = []
    monkeypatch.setattr(docs_intel, "scrape_page",
                        lambda url, env=None: calls.append(("docs", url)) or "official docs")
    monkeypatch.setattr(adapter_gen, "generate_adapter",
                        lambda name, docs, env=None: calls.append(("adapter", name, docs)) or
                        Candidate(name, name, "", "local_tool", [], "def extract(_): return {}"))

    orchestrator._prepare_generated_adapters([{
        "name": "paddleocr", "docs_url": "https://example.com/paddleocr",
        "pricing_url": "", "kind": "local_tool", "use_fallback": False,
    }])

    assert calls == [("docs", "https://example.com/paddleocr"),
                     ("adapter", "paddleocr", "official docs")]
    assert orchestrator.ctx.candidates["paddleocr"].docs_url == "https://example.com/paddleocr"


def test_phase_reporting_never_echoes_a_candidate_name_the_run_did_not_admit(
    tmp_path, monkeypatch
):
    events: list = []
    orchestrator, _dataset = _tool_loop_orchestrator(
        tmp_path, monkeypatch, events, "spawn_sandbox", {}
    )
    orchestrator.ctx.allowed_candidate_names.add("tesseract")

    orchestrator._track_phase("spawn_sandbox", {"label": "tesseract"})
    orchestrator._track_phase("spawn_sandbox", {"label": "invented-by-the-model"})
    orchestrator._track_phase("web_search", {"query": "anything"})

    assert [data for _event, data in events] == [
        {"phase": "PROVISIONING", "candidates": {"tesseract": "provisioning"}},
        {"phase": "PROVISIONING", "candidates": {}},
    ]


# ------------------------------------------------- intake spec retry (no network)
class _ScriptedChatClient:
    """Replays prepared assistant texts; no network and no tool calls."""

    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        if not self._texts:
            raise AssertionError("intake asked for more turns than expected")
        self.calls += 1
        message = SimpleNamespace(content=self._texts.pop(0), tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _ToolThenReplyChatClient:
    """Captures the next provider request without touching a real provider."""

    def __init__(self):
        self.requests = []
        self._turn = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        self._turn += 1
        if self._turn == 1:
            call = SimpleNamespace(
                id="call-docs",
                function=SimpleNamespace(
                    name="scrape_docs",
                    arguments=json.dumps({"url": "https://docs.example.test/guide?token=hidden"}),
                ),
            )
            message = SimpleNamespace(
                content=None,
                tool_calls=[call],
                model_dump=lambda **_kwargs: {"role": "assistant", "content": "", "tool_calls": []},
            )
        else:
            message = SimpleNamespace(content="Docs reviewed.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


_INTAKE_SPEC_REPLY = """Here is the benchmark.

```json
{"benchmark_type": "extraction", "category": "invoice extraction",
 "fields": ["invoice_number", "date", "vendor", "total"],
 "candidates": [{"name": "tesseract", "kind": "local_tool", "use_fallback": true},
                {"name": "easyocr", "kind": "local_tool", "use_fallback": true}]}
```
"""


def _intake_orchestrator(tmp_path, monkeypatch, events, texts, dataset_available=True):
    from engine import agent

    client = _ScriptedChatClient(texts)
    orchestrator = Orchestrator(
        "intake",
        str(tmp_path / "run"),
        lambda event, data: events.append((event, data)),
        dataset_available=dataset_available,
    )
    monkeypatch.setattr(agent, "_orchestrator_client", lambda _env: client)
    monkeypatch.setattr(agent, "_orchestrator_model", lambda _env: "test-model")
    return orchestrator, client


def test_complete_extraction_request_retries_once_and_emits_only_the_spec(
    tmp_path, monkeypatch
):
    """A complete request must not cost the user a second confirmation turn.

    Regression: intake returned the first assistant reply verbatim, so a
    request that already named the dataset, the objective, and the candidates
    got a question back instead of a spec.
    """
    events: list = []
    orchestrator, client = _intake_orchestrator(
        tmp_path,
        monkeypatch,
        events,
        ["Which fields should I score, and shall I proceed?", _INTAKE_SPEC_REPLY],
    )

    orchestrator.chat(
        "Benchmark invoice extraction accuracy on my labelled dataset "
        "using tesseract and easyocr."
    )

    assert client.calls == 2
    deltas = [data["text"] for event, data in events if event == "delta"]
    assert len(deltas) == 1 and "```json" in deltas[0]
    assert "shall I proceed" not in " ".join(deltas)
    artifacts = [data for event, data in events if event == "artifact"]
    assert len(artifacts) == 1
    assert artifacts[0]["spec"]["benchmark_type"] == "extraction"
    assert [c["name"] for c in artifacts[0]["spec"]["candidates"]] == [
        "tesseract",
        "easyocr",
    ]


def test_commented_intake_json_is_normalized_before_spec_confirmation(tmp_path, monkeypatch):
    """Model-style comments and trailing commas must not strand a runnable intake."""
    events: list = []
    reply = """```json
    {"benchmark_type":"extraction", "category":"Invoice OCR",
     "fields":["invoice_number", "date", "vendor", "total",],
     "candidates":[
       {"name":"PaddleOCR", "docs_url":"https://www.paddlepaddle.org.cn/en/documentation",
        "pricing_url":"Open-source", // optional placeholder
        "kind":"local_tool", "use_fallback":false,},
       {"name":"Tesseract", "docs_url":"https://tesseract-ocr.github.io/tessdoc/",
        "pricing_url":"", /* built in */ "kind":"local_tool", "use_fallback":true,},
     ],}
    ```"""
    orchestrator, _client = _intake_orchestrator(tmp_path, monkeypatch, events, [reply])

    orchestrator.chat(
        "Benchmark PaddleOCR and Tesseract on exact match accuracy and latency for "
        "invoice number, date, vendor, and total using the attached labelled dataset."
    )

    artifact = next(data for event, data in events if event == "artifact" and data["kind"] == "spec")
    assert artifact["spec"]["candidates"][0]["name"] == "PaddleOCR"
    assert artifact["spec"]["candidates"][0]["pricing_url"] == ""
    assert any(event == "state" and data["phase"] == "SPEC_CONFIRM" for event, data in events)


def test_incomplete_request_still_gets_its_clarifying_question(tmp_path, monkeypatch):
    """Nothing is retried when the request does not name the candidates."""
    events: list = []
    orchestrator, client = _intake_orchestrator(
        tmp_path, monkeypatch, events, ["Which tools would you like to compare?"]
    )

    orchestrator.chat("I want to benchmark extraction on my documents.")

    assert client.calls == 1
    assert [data["text"] for event, data in events if event == "delta"] == [
        "Which tools would you like to compare?"
    ]
    assert [event for event, _data in events if event == "artifact"] == []


def test_retry_is_bounded_to_one_extra_turn(tmp_path, monkeypatch):
    """A model that keeps asking is surfaced, not looped over."""
    events: list = []
    orchestrator, client = _intake_orchestrator(
        tmp_path,
        monkeypatch,
        events,
        ["Which fields matter?", "I still need the field list."],
    )

    orchestrator.chat(
        "Compare tesseract and easyocr on OCR extraction over the attached dataset."
    )

    assert client.calls == 2
    assert [data["text"] for event, data in events if event == "delta"] == [
        "I still need the field list."
    ]


def test_assessment_spec_with_a_malformed_docs_url_is_never_confirmed(tmp_path, monkeypatch):
    """A required docs_url the run schema rejects must not reach SPEC_CONFIRM.

    Regression: intake emitted whatever JSON the model produced, so a candidate
    whose only documentation address was not a public HTTP(S) URL produced a
    spec artifact that POST /api/sessions/{id}/run then rejected with 422.
    """
    events: list = []
    reply = """Proposed benchmark.

```json
{"benchmark_type": "tool_assessment", "category": "CRM sync",
 "objective": "sync accounts nightly",
 "candidates": [{"name": "alpha", "display_name": "Alpha",
                 "docs_url": "docs.alpha.example/guide", "pricing_url": "Open-source",
                 "kind": "saas"}]}
```"""
    orchestrator, _client = _intake_orchestrator(
        tmp_path, monkeypatch, events, [reply], dataset_available=False
    )

    orchestrator.chat("Compare CRM sync tools for us.")

    assert [data for event, data in events
            if event == "artifact" and data.get("kind") == "spec"] == []
    assert not any(event == "state" and data["phase"] == "SPEC_CONFIRM"
                   for event, data in events)
    # The reply itself is still returned, so the user can correct the request.
    assert [data["text"] for event, data in events if event == "delta"] == [reply]


def test_assessment_intake_placeholder_pricing_survives_as_an_empty_string(
    tmp_path, monkeypatch
):
    """The literal "Open-source" placeholder normalizes instead of blocking a run."""
    events: list = []
    reply = """```json
{"benchmark_type": "tool_assessment", "category": "CRM sync",
 "objective": "sync accounts nightly",
 "candidates": [{"name": "alpha", "display_name": "Alpha",
                 "docs_url": "https://docs.alpha.example/guide",
                 "pricing_url": "Open-source", "kind": "saas"}]}
```"""
    orchestrator, _client = _intake_orchestrator(
        tmp_path, monkeypatch, events, [reply], dataset_available=False
    )

    orchestrator.chat("Compare CRM sync tools for us.")

    artifact = next(data for event, data in events
                    if event == "artifact" and data.get("kind") == "spec")
    assert artifact["spec"]["candidates"][0]["pricing_url"] == ""
    assert artifact["spec"]["candidates"][0]["docs_url"] == "https://docs.alpha.example/guide"
    assert any(event == "state" and data["phase"] == "SPEC_CONFIRM" for event, data in events)


def test_chat_tool_history_bounds_scraped_pages_and_preserves_safe_citation(
    tmp_path, monkeypatch
):
    """Several docs pages must not inflate the next provider context."""
    from engine import agent

    events: list = []
    client = _ToolThenReplyChatClient()
    orchestrator = Orchestrator(
        "history", str(tmp_path / "run"), lambda event, data: events.append((event, data))
    )
    monkeypatch.setattr(agent, "_orchestrator_client", lambda _env: client)
    monkeypatch.setattr(agent, "_orchestrator_model", lambda _env: "test-model")
    secret = "provider-secret-value"
    safe_evidence_url = "https://docs.example.test/guide/install"
    oversized = (
        f"C:\\Users\\operator\\private {secret} {safe_evidence_url} "
        + ("evidence " * 20_000)
    )
    monkeypatch.setattr(agent, "dispatch_tool", lambda *_args: json.dumps({"page": oversized}))
    orchestrator.ctx.env_passthrough = {"PROVIDER_API_KEY": secret}

    orchestrator.chat("Find the OCR integration guide.")

    tool_messages = [m for m in client.requests[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    history = json.loads(tool_messages[0]["content"])
    assert len(tool_messages[0]["content"]) <= agent.MAX_CHAT_TOOL_RESULT_CHARS + 200
    assert history["truncated"] is True
    assert history["citation_url"] == "https://docs.example.test/guide"
    assert safe_evidence_url in history["result_excerpt"]
    assert secret not in tool_messages[0]["content"]
    assert "C:\\Users" not in tool_messages[0]["content"]


def test_history_url_removes_userinfo_and_query(tmp_path):
    orchestrator = Orchestrator("history-url", str(tmp_path / "run"), lambda *_args: None)

    assert orchestrator._history_url(
        "https://user:password@Docs.Example.test:8443/guide?token=secret#part"
    ) == "https://docs.example.test:8443/guide"
