"""One bad sample must not kill a candidate.

A real eight-candidate run shipped three "Assessment unavailable" rows — a 37%
failure rate. Not one was evidence about a tool: one was a scrape that failed a
single attempt, two were single malformed JSON replies. The loop's failover was
all-or-nothing per batch — as soon as ANY candidate parsed, the batch returned
and the malformed ones were frozen into permanent failures with providers still
unasked.

These pin the replacement: the candidate is the unit of retry, a validation
failure is fed back so the retry can correct rather than repeat, and a candidate
is reported failed only after every provider has had every sweep at it.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from engine import llm_clients
from engine.tool_assessment import MAX_ASSESSMENT_SWEEPS, assess_documentation_batch

ENV = {"DOUBLEWORD_API_KEY": "hidden", "OPENROUTER_API_KEY": "hidden"}


def good_plan() -> str:
    return json.dumps({
        "implementable": True, "execution_mode": "comparison_only",
        "reason": "documented", "documentation_quality": 80,
        "integration_feasibility": 70, "auth_clarity": 60, "setup_complexity": 2,
        "build_commands": [], "verification_code": "", "evidence": ["fact"],
    })


def _response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _serve(monkeypatch, handler):
    calls = []

    async def fake(provider, requests, model=None, env=None):
        calls.append((provider, [json.dumps(r["messages"]) for r in requests]))
        return [handler(provider, request, len(calls)) for request in requests]

    monkeypatch.setattr(llm_clients, "provider_chat_completions", fake)
    return calls


def test_one_malformed_reply_is_retried_not_frozen(monkeypatch):
    """The 37% run: alpha parsed, beta did not, and beta was never asked again."""
    def handler(provider, request, call_no):
        body = json.dumps(request["messages"])
        if "beta docs" in body and call_no == 1:
            return _response("{ not json")
        return _response(good_plan())

    calls = _serve(monkeypatch, handler)
    results = assess_documentation_batch(
        [{"name": "alpha", "docs_text": "alpha docs"},
         {"name": "beta", "docs_text": "beta docs"}],
        "objective", env=dict(ENV),
    )

    assert "plan" in results["alpha"]
    assert "plan" in results["beta"], "a single bad sample must not be terminal"
    # The retry re-requested only the unresolved candidate.
    assert len(calls[1][1]) == 1


def test_the_validation_error_is_fed_back_to_the_retry(monkeypatch):
    """A blind retry of a deterministic request repeats the mistake."""
    seen = {}

    def handler(provider, request, call_no):
        body = json.dumps(request["messages"])
        if "failed validation" in body:
            seen["corrective"] = True
            return _response(good_plan())
        return _response(json.dumps({"nope": 1}))

    _serve(monkeypatch, handler)
    results = assess_documentation_batch(
        [{"name": "alpha", "docs_text": "alpha docs"}], "objective", env=dict(ENV),
    )

    assert seen.get("corrective"), "the retry must carry the validation error"
    assert "plan" in results["alpha"]


def test_a_candidate_fails_only_after_every_provider_and_sweep(monkeypatch):
    calls = _serve(monkeypatch, lambda p, r, n: _response("never valid"))
    results = assess_documentation_batch(
        [{"name": "alpha", "docs_text": "alpha docs"}], "objective", env=dict(ENV),
    )

    assert "error" in results["alpha"]
    # Both providers, both sweeps: 2 providers x MAX_ASSESSMENT_SWEEPS.
    assert len(calls) == 2 * MAX_ASSESSMENT_SWEEPS


def test_resolved_candidates_are_not_rerequested(monkeypatch):
    """Retrying a candidate that already has a plan spends money for nothing."""
    def handler(provider, request, call_no):
        if "beta docs" in json.dumps(request["messages"]):
            return _response("bad")
        return _response(good_plan())

    calls = _serve(monkeypatch, handler)
    assess_documentation_batch(
        [{"name": "alpha", "docs_text": "alpha docs"},
         {"name": "beta", "docs_text": "beta docs"}],
        "objective", env=dict(ENV),
    )

    for _provider, bodies in calls[1:]:
        assert all("alpha docs" not in body for body in bodies)


def test_a_dead_provider_still_hands_the_whole_batch_to_the_next(monkeypatch):
    """The original failover semantics survive the restructure."""
    providers_called = []

    async def fake(provider, requests, model=None, env=None):
        providers_called.append(provider)
        if provider == "doubleword":
            raise RuntimeError("unreachable")
        return [_response(good_plan()) for _ in requests]

    monkeypatch.setattr(llm_clients, "provider_chat_completions", fake)
    results = assess_documentation_batch(
        [{"name": "alpha", "docs_text": "alpha docs"}], "objective", env=dict(ENV),
    )

    assert providers_called[:2] == ["doubleword", "openrouter"]
    assert "plan" in results["alpha"]


def test_every_provider_raising_is_still_a_loud_failure(monkeypatch):
    async def fake(provider, requests, model=None, env=None):
        raise RuntimeError("down")

    monkeypatch.setattr(llm_clients, "provider_chat_completions", fake)
    with pytest.raises(RuntimeError, match="every configured assessment provider failed"):
        assess_documentation_batch(
            [{"name": "alpha", "docs_text": "alpha docs"}], "objective", env=dict(ENV),
        )


# ---------------------------------------------------------------- scrape retry

import shutil
import uuid
from pathlib import Path

from engine import agent, tool_assessment
from engine.tool_assessment import validate_plan


def _plan_json():
    return {
        "implementable": False, "execution_mode": "comparison_only",
        "reason": "docs only", "documentation_quality": 40,
        "integration_feasibility": 30, "auth_clarity": 20, "setup_complexity": 3,
        "build_commands": [], "verification_code": "", "evidence": [],
    }


def _run(monkeypatch, dispatch):
    events = []
    run_dir = Path("runs") / f"test_scrape_retry_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool", dispatch)
    monkeypatch.setattr(
        tool_assessment, "assess_documentation_batch",
        lambda candidates, *_a, **_k: {
            c["name"]: {"plan": validate_plan(_plan_json())} for c in candidates
        },
    )
    orchestrator = agent.Orchestrator(
        "test-retry", str(run_dir), lambda event, data: events.append((event, data)))
    try:
        return orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "c", "objective": "o",
            "candidates": [{"name": "example", "display_name": "Example",
                            "docs_url": "https://example.com/docs", "kind": "saas"}],
        })
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_a_scrape_that_fails_once_is_retried_and_the_candidate_survives(monkeypatch):
    """The third unavailable row: one lost race wrote a candidate off entirely."""
    attempts = []

    def flaky(name, args, ctx):
        attempts.append(name)
        if len(attempts) == 1:
            return json.dumps({"error": "documentation retrieval failed"})
        return json.dumps("official docs")

    metrics = _run(monkeypatch, flaky)

    assert len(attempts) == 2
    assert metrics["example"]["rating"] is not None, "one lost race must not be terminal"


def test_a_scrape_that_fails_twice_is_reported_not_retried_forever(monkeypatch):
    attempts = []

    def dead(name, args, ctx):
        attempts.append(name)
        return json.dumps({"error": "documentation retrieval failed"})

    metrics = _run(monkeypatch, dead)

    assert len(attempts) == 2, "exactly one retry, then the honest failure row"
    assert metrics["example"]["rating"] is None
