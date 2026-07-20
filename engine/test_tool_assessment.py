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


def test_unavailable_docs_return_rating_without_daytona():
    result = unavailable_result("documentation could not be scraped")
    assert result["rating"] == 0
    assert result["verification_status"] == "not_implementable"
    assert result["daytona_triggered"] is False


def test_scores_are_bounded():
    with pytest.raises(ValueError, match="documentation_quality"):
        validate_plan(plan(documentation_quality=101))


def test_real_intake_extracts_nested_candidate_spec():
    spec = Orchestrator._extract_spec(
        'Ready.\n```json\n{"category":"CRM","objective":"sync accounts",'
        '"candidates":[{"name":"alpha","docs_url":"https://example.com/docs"}]}\n```'
    )
    assert spec is not None
    assert spec["benchmark_type"] == "tool_assessment"
    assert spec["candidates"][0]["name"] == "alpha"


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
    )

    assert captured["model"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert len(captured["requests"]) == 2
    prompt = captured["requests"][0]["messages"][1]["content"]
    assert "ACME_API_KEY" in prompt
    assert "another-hidden-value" not in prompt
    assert results["alpha"]["plan"]["implementable"] is True


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
