import asyncio
import json
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.agent import Orchestrator
from engine import agent, llm_clients, tool_assessment
from engine.tool_assessment import (
    assess_documentation_batch,
    result_from_plan,
    unavailable_result,
    validate_plan,
)


def plan(**overrides):
    value = {
        "implementable": True,
        "reason": "The official SDK documents installation and client construction.",
        "documentation_quality": 80,
        "integration_feasibility": 84,
        "auth_clarity": 70,
        "setup_complexity": 2,
        "build_commands": ["pip install example-sdk"],
        "verification_code": "import example\nprint('PROOFBENCH_OK')",
        "evidence": ["The SDK has a documented Python package."],
    }
    value.update(overrides)
    return value


def test_verified_implementation_uses_daytona_and_gets_bonus():
    result = result_from_plan(validate_plan(plan()), "passed", True)
    assert result["rating"] == 90
    assert result["implementable"] is True
    assert result["daytona_triggered"] is True


def test_non_implementable_plan_cannot_trigger_daytona():
    value = validate_plan(plan(
        implementable=False,
        build_commands=["pip install should-be-removed"],
        verification_code="print('PROOFBENCH_OK')",
        integration_feasibility=40,
    ))
    result = result_from_plan(value, "not_implementable", False)
    assert value["build_commands"] == []
    assert value["verification_code"] == ""
    assert result["rating"] <= 49
    assert result["daytona_triggered"] is False


def test_implementable_plan_requires_verification_code():
    with pytest.raises(ValueError, match="verification_code"):
        validate_plan(plan(verification_code=""))


def test_unavailable_docs_withhold_scores_instead_of_faking_zeros():
    """A failure is not evidence about the tool, so no number is invented."""
    result = unavailable_result("documentation could not be scraped")
    assert result["rating"] is None
    assert result["suitability"] is None
    assert result["documentation_quality"] is None
    assert result["integration_feasibility"] is None
    assert result["auth_clarity"] is None
    assert result["setup_complexity"] is None
    assert result["assessment_basis"] == "unavailable"
    assert result["verification_status"] == "unavailable"
    assert result["daytona_triggered"] is False


def test_scores_are_bounded():
    with pytest.raises(ValueError, match="documentation_quality"):
        validate_plan(plan(documentation_quality=101))


# --------------------------------------------------- execution mode semantics


def test_comparison_only_candidate_keeps_a_legitimate_score_and_is_never_executed():
    """A SaaS product is scored from docs and is not penalised for being unrunnable."""
    value = validate_plan(plan(
        execution_mode="comparison_only",
        build_commands=["pip install should-be-removed"],
        verification_code="print('PROOFBENCH_OK')",
    ))
    # Nothing runnable survives validation for a comparison-only candidate.
    assert value["build_commands"] == []
    assert value["verification_code"] == ""

    result = result_from_plan(value, "not_applicable", False)
    # 0.30*80 + 0.50*84 + 0.20*70 == 80, with no unrunnability penalty.
    assert result["rating"] == 80
    assert result["suitability"] == 80
    assert result["execution_mode"] == "comparison_only"
    assert result["assessment_basis"] == "documentation_evidence"
    assert result["daytona_triggered"] is False
    assert result["verification_status"] == "not_applicable"


def test_sandbox_verified_candidate_reports_execution_basis():
    result = result_from_plan(validate_plan(plan()), "passed", True)
    assert result["execution_mode"] == "sandbox_verifiable"
    assert result["assessment_basis"] == "sandbox_execution"


def test_execution_mode_is_inferred_when_the_model_omits_it():
    assert validate_plan(plan())["execution_mode"] == "sandbox_verifiable"
    assert validate_plan(plan(
        implementable=False, build_commands=[], verification_code="",
    ))["execution_mode"] == "comparison_only"


def test_unknown_execution_mode_is_rejected():
    with pytest.raises(ValueError, match="execution_mode"):
        validate_plan(plan(execution_mode="pretend_we_ran_it"))


def test_comparison_only_candidate_never_provisions_a_sandbox(monkeypatch):
    """The runner must not acquire Daytona for a documentation-only comparison."""
    events = []
    run_dir = Path("runs") / f"test_comparison_only_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool", lambda *_a, **_k: json.dumps("official docs"))
    monkeypatch.setattr(
        tool_assessment,
        "assess_documentation_batch",
        lambda candidates, *_a, **_k: {
            candidate["name"]: {"plan": validate_plan(plan(execution_mode="comparison_only"))}
            for candidate in candidates
        },
    )
    orchestrator = Orchestrator(
        "test-comparison", str(run_dir), lambda event, data: events.append((event, data))
    )

    def fail_if_acquired(_name):
        raise AssertionError("comparison-only candidates must never reach Daytona")

    monkeypatch.setattr(orchestrator.pool, "acquire", fail_if_acquired)
    try:
        metrics = orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "CRM",
            "objective": "Compare hosted CRM integrations",
            "candidates": [{"name": "example", "display_name": "Example",
                            "docs_url": "https://example.com/docs", "kind": "saas"}],
        })
        row = metrics["example"]
        assert row["daytona_triggered"] is False
        assert row["assessment_basis"] == "documentation_evidence"
        assert isinstance(row["rating"], int) and 0 <= row["rating"] <= 100
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_provider_failure_withholds_scores_instead_of_zeroing_every_candidate(monkeypatch):
    """An assessment outage must not read as 'every tool scored zero'."""
    events = []
    run_dir = Path("runs") / f"test_provider_failure_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool", lambda *_a, **_k: json.dumps("official docs"))

    def explode(*_args, **_kwargs):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(tool_assessment, "assess_documentation_batch", explode)
    orchestrator = Orchestrator(
        "test-outage", str(run_dir), lambda event, data: events.append((event, data))
    )
    try:
        metrics = orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "CRM",
            "objective": "Compare hosted CRM integrations",
            "candidates": [
                {"name": "alpha", "docs_url": "https://example.com/a"},
                {"name": "beta", "docs_url": "https://example.com/b"},
            ],
        })
        for row in metrics.values():
            assert row["rating"] is None
            assert row["documentation_quality"] is None
            assert row["assessment_basis"] == "unavailable"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_assessment_falls_back_to_the_next_configured_provider(monkeypatch):
    """A dead primary provider hands off rather than collapsing the whole batch."""
    calls = []

    async def fake_provider_completions(provider, requests, model=None, env=None):
        calls.append(provider)
        if provider == "doubleword":
            raise RuntimeError("doubleword unreachable")
        content = json.dumps(plan(execution_mode="comparison_only"))
        return [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "provider_chat_completions", fake_provider_completions)
    results = assess_documentation_batch(
        [{"name": "alpha", "docs_text": "Alpha docs"}],
        "compare integrations",
        env={"DOUBLEWORD_API_KEY": "hidden", "OPENROUTER_API_KEY": "hidden"},
    )

    assert calls == ["doubleword", "openrouter"]
    assert results["alpha"]["plan"]["execution_mode"] == "comparison_only"


def test_assessment_timeout_falls_back_to_the_next_provider(monkeypatch):
    """A provider that never completes cannot hold the whole pipeline open."""
    calls = []

    async def fake_provider_completions(provider, requests, model=None, env=None):
        calls.append(provider)
        if provider == "doubleword":
            await asyncio.sleep(0.05)
        content = json.dumps(plan(execution_mode="comparison_only"))
        return [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "provider_chat_completions", fake_provider_completions)
    results = assess_documentation_batch(
        [{"name": "alpha", "docs_text": "Alpha docs"}],
        "compare integrations",
        env={
            "DOUBLEWORD_API_KEY": "hidden",
            "OPENROUTER_API_KEY": "hidden",
            "ASSESSMENT_PROVIDER_TIMEOUT_SECONDS": "0.01",
        },
    )

    assert calls == ["doubleword", "openrouter"]
    assert results["alpha"]["plan"]["execution_mode"] == "comparison_only"


def test_assessment_requires_a_configured_provider():
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        assess_documentation_batch(
            [{"name": "alpha", "docs_text": "docs"}], "objective", env={}
        )


def test_openrouter_alone_serves_every_llm_capability():
    from engine.llm_clients import capability_providers, resolve_provider

    env = {"OPENROUTER_API_KEY": "hidden-test-value"}
    for capability in ("orchestration", "assessment", "report", "codegen"):
        assert capability_providers(capability, env) == ("openrouter",)
        assert resolve_provider(capability, env) == "openrouter"


def _intake(dataset_available=False, run_dir="runs"):
    return Orchestrator(
        f"intake-{uuid.uuid4().hex[:8]}", run_dir, lambda _event, _data: None,
        dataset_available=dataset_available,
    )


def test_real_intake_extracts_nested_candidate_spec(tmp_path):
    spec = _intake(run_dir=str(tmp_path))._extract_spec(
        'Ready.\n```json\n{"category":"CRM","objective":"sync accounts",'
        '"candidates":[{"name":"alpha","docs_url":"https://example.com/docs"}]}\n```'
    )
    assert spec is not None
    assert spec["benchmark_type"] == "tool_assessment"
    assert spec["candidates"][0]["name"] == "alpha"


def test_intake_builds_an_extraction_spec_when_labelled_data_is_bound(tmp_path):
    """An OCR objective plus ground truth is a scored run, not a docs rating."""
    text = ('```json\n{"category":"Invoice OCR",'
            '"fields":["invoice_number","date","vendor","total"],'
            '"candidates":[{"name":"tesseract","kind":"local_tool","use_fallback":true}]}\n```')

    spec = _intake(dataset_available=True, run_dir=str(tmp_path))._extract_spec(text)
    assert spec["benchmark_type"] == "extraction"
    assert spec["fields"] == ["invoice_number", "date", "vendor", "total"]

    # Without labelled data there is nothing to score. The old downgrade would
    # have emitted an assessment missing its mandatory docs URL, which the run
    # endpoint correctly rejected; intake must now withhold that invalid spec.
    downgraded = _intake(dataset_available=False, run_dir=str(tmp_path))._extract_spec(text)
    assert downgraded is None


def test_assessment_intake_normalizes_pricing_placeholders_and_rejects_bad_docs(tmp_path):
    intake = _intake(run_dir=str(tmp_path))
    valid = intake._extract_spec(
        '```json\n{"benchmark_type":"tool_assessment","category":"HTTP clients",'
        '"objective":"compare integrations","candidates":[{"name":"Requests/HTTPX",'
        '"display_name":"Requests / HTTPX","docs_url":"https://www.python-httpx.org/",'
        '"pricing_url":"Open-source","kind":"saas"}]}\n```'
    )
    assert valid is not None
    assert valid["candidates"][0]["name"] == "Requests-HTTPX"
    assert valid["candidates"][0]["pricing_url"] == ""

    invalid = intake._extract_spec(
        '```json\n{"benchmark_type":"tool_assessment","category":"HTTP clients",'
        '"objective":"compare integrations","candidates":[{"name":"httpx",'
        '"display_name":"HTTPX","docs_url":"Open-source","kind":"saas"}]}\n```'
    )
    assert invalid is None


def test_extraction_intake_prompt_appears_only_with_labelled_data():
    from engine.agent import intake_system

    assert "extraction" in intake_system(True)
    assert '"benchmark_type": "extraction"' in intake_system(True)
    assert '"benchmark_type": "extraction"' not in intake_system(False)


def test_real_assessments_use_doubleword_autobatcher_and_configured_model(monkeypatch):
    captured = {}

    async def fake_batch(requests, model=None, env=None):
        captured.update({"requests": requests, "model": model, "env": env})
        content = json.dumps(plan())
        return [
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "batch_chat_completions", fake_batch)
    results = assess_documentation_batch(
        [
            {"name": "alpha", "docs_text": "Alpha SDK docs"},
            {"name": "beta", "docs_text": "Beta API docs"},
        ],
        "compare integrations",
        env={
            "DOUBLEWORD_API_KEY": "hidden-test-value",
            "DOUBLEWORD_MODEL": "deepseek-ai/DeepSeek-V4-Pro",
            "ACME_API_KEY": "another-hidden-value",
        },
        entitled_credentials=("ACME_API_KEY",),
    )

    assert captured["model"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert len(captured["requests"]) == 2
    prompt = captured["requests"][0]["messages"][1]["content"]
    assert "ACME_API_KEY" in prompt
    assert "another-hidden-value" not in prompt
    assert results["alpha"]["plan"]["implementable"] is True


def test_advertised_credentials_are_exactly_the_verification_entitlements(monkeypatch):
    """A plan may only rely on variables the verification sandbox will supply."""
    captured = {}

    async def fake_batch(requests, model=None, env=None):
        captured["requests"] = requests
        content = json.dumps(plan())
        return [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "batch_chat_completions", fake_batch)
    env = {
        "DOUBLEWORD_API_KEY": "hidden-test-value",
        "OPENAI_API_KEY": "hidden-test-value",
        "OXYLABS_PASSWORD": "hidden-test-value",
    }
    assess_documentation_batch(
        [{"name": "alpha", "docs_text": "Alpha SDK docs"}],
        "compare integrations",
        env=env,
        entitled_credentials=tool_assessment.ASSESSMENT_VERIFICATION_ENTITLEMENTS,
    )

    prompt = captured["requests"][0]["messages"][1]["content"]
    # Generated verification code is entitled to nothing, so nothing is offered.
    assert tool_assessment.ASSESSMENT_VERIFICATION_ENTITLEMENTS == frozenset()
    assert "(none)" in prompt
    for name in env:
        assert name not in prompt


def test_assessment_verification_code_receives_no_credentials():
    """env_prelude injects nothing under the assessment entitlement set."""
    from engine.tools import env_prelude

    code = 'import os\nprint(os.environ["OPENAI_API_KEY"])'
    injected = env_prelude(
        code,
        {"OPENAI_API_KEY": "hidden-test-value"},
        tool_assessment.ASSESSMENT_VERIFICATION_ENTITLEMENTS,
    )
    assert injected == code
    assert "hidden-test-value" not in injected


def test_real_runner_skips_daytona_when_docs_are_not_implementable(monkeypatch):
    events = []
    run_dir = Path("runs") / f"test_tool_assessment_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool", lambda *_args, **_kwargs: json.dumps("official docs"))
    monkeypatch.setattr(
        tool_assessment,
        "assess_documentation_batch",
        lambda candidates, *_args, **_kwargs: {
            candidate["name"]: {"plan": validate_plan(plan(
                implementable=False,
                build_commands=[],
                verification_code="",
                integration_feasibility=30,
            ))}
            for candidate in candidates
        },
    )
    orchestrator = Orchestrator("test-real", str(run_dir), lambda event, data: events.append((event, data)))

    def fail_if_acquired(_name):
        raise AssertionError("Daytona must not be acquired for a non-implementable tool")

    monkeypatch.setattr(orchestrator.pool, "acquire", fail_if_acquired)
    try:
        metrics = orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "Collaboration",
            "objective": "Assess an internal messaging integration",
            "candidates": [{
                "name": "example",
                "display_name": "Example",
                "docs_url": "https://example.com/docs",
                "kind": "saas",
            }],
        })

        assert metrics["example"]["daytona_triggered"] is False
        assert metrics["example"]["verification_status"] == "not_implementable"
        assert any(event == "artifact" and data.get("kind") == "results" for event, data in events)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
