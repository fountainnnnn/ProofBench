"""A distinct model, or no review at all — never the model reviewing itself.

The whole point of supervision is that the identity which produced an artifact
is not the identity that corrects it: a model that missed a violation waves it
through a second time, and one that cut a corner defends the corner. These tests
pin that the resolver refuses same-identity review, that the correction is one
bounded toolless call validated by the primary path's own parser, that a failure
erases no evidence, and that the intake spec-recovery path uses all of it to turn
a researched turn's prose into a real spec — or fails out loud rather than
presenting a shortlist as a finished benchmark.
"""
from __future__ import annotations

import json
import types

from engine import agent as agent_mod
from engine import supervisor
from engine.llm_clients import (
    ModelIdentity,
    primary_identity,
    provider_model,
    supervisor_identity,
)


# --------------------------------------------------------------- identity rules

def test_the_supervisor_is_never_the_primary_identity():
    """One provider, no alternate model: there is no distinct reviewer, so None."""
    env = {"DEEPSEEK_API_KEY": "sk-only"}
    primary = primary_identity("assessment", env)

    assert primary == ModelIdentity("deepseek", "deepseek-v4-flash")
    assert supervisor_identity("assessment", env) is None


def test_a_second_provider_is_picked_as_the_distinct_reviewer():
    env = {"DEEPSEEK_API_KEY": "sk-primary", "MOONSHOT_API_KEY": "sk-reviewer"}

    reviewer = supervisor_identity("assessment", env)

    assert reviewer is not None
    # Primary assessment is DeepSeek; the reviewer is a different provider.
    assert reviewer.provider == "moonshot"
    assert not reviewer.same_as(primary_identity("assessment", env))


def test_the_same_provider_supervises_only_with_an_explicit_alternate_model():
    """A pin back onto the primary provider needs a genuinely different model."""
    env = {
        "DEEPSEEK_API_KEY": "sk-only",
        "SUPERVISOR_PROVIDER": "deepseek",
    }
    # Same provider, no alternate model -> not distinct -> refused.
    assert supervisor_identity("orchestration", env) is None

    env["SUPERVISOR_MODEL"] = "deepseek-r1-distinct"
    reviewer = supervisor_identity("orchestration", env)
    assert reviewer == ModelIdentity("deepseek", "deepseek-r1-distinct")


def test_an_alternate_model_equal_to_the_primary_is_not_distinct():
    env = {
        "DEEPSEEK_API_KEY": "sk-only",
        "SUPERVISOR_PROVIDER": "deepseek",
        # Same model the primary already runs, spelled differently in case: still
        # the same identity, so still refused.
        "SUPERVISOR_MODEL": "DeepSeek-V4-Flash",
    }
    assert supervisor_identity("orchestration", env) is None


def test_an_unconfigured_pin_yields_no_supervisor_rather_than_walking_off():
    env = {"DEEPSEEK_API_KEY": "sk-only", "SUPERVISOR_PROVIDER": "openai"}
    # openai is not configured; a pin is honoured exactly or not at all.
    assert supervisor_identity("orchestration", env) is None


# ------------------------------------------ independence against the REAL producer

def test_the_reviewer_excludes_the_actual_producer_after_failover(monkeypatch):
    """Configured primary fails, a fallback produces — the reviewer resolution is
    given the ACTUAL producer and must never return it, even though it differs
    from the configured primary. Only two providers exist here, so once the real
    producer is excluded there is no distinct reviewer, and None is the honest
    answer rather than the fallback reviewing itself."""
    env = {"MOONSHOT_API_KEY": "sk-down", "DEEPSEEK_API_KEY": "sk-produced"}

    def fake_chat_client(provider, env=None):
        if provider == "moonshot":
            raise RuntimeError("primary rate limited")
        return _Recording("prose, but no spec")  # deepseek is the real producer

    monkeypatch.setattr("engine.llm_clients.chat_client", fake_chat_client)

    sink: list = []
    agent_mod._orchestrator_complete(
        env, _producer_sink=sink, messages=[{"role": "user", "content": "hi"}])
    assert sink == [ModelIdentity("deepseek", "deepseek-v4-flash")]

    # Configured primary is moonshot; the artifact was actually produced by
    # deepseek. Independence is against deepseek, so the reviewer is neither.
    reviewer = supervisor_identity("orchestration", env, exclude=sink)
    assert reviewer is None
    assert reviewer != ModelIdentity("deepseek", "deepseek-v4-flash")


def test_a_third_provider_reviews_when_primary_and_producer_are_both_excluded():
    """The producer differs from the configured primary; a genuinely distinct
    third provider still serves, never the fallback that produced the artifact."""
    env = {"MOONSHOT_API_KEY": "1", "DEEPSEEK_API_KEY": "1", "OPENAI_API_KEY": "1"}
    producer = ModelIdentity("deepseek", provider_model("deepseek", env))

    reviewer = supervisor_identity("orchestration", env, exclude=[producer])

    assert reviewer is not None
    assert not reviewer.same_as(producer)                       # not the producer
    assert not reviewer.same_as(primary_identity("orchestration", env))  # not primary
    assert reviewer.provider == "openai"


# ------------------------------------------ explicit model with no provider pin

def test_an_explicit_model_without_a_pin_binds_to_the_primary_provider():
    """SUPERVISOR_MODEL alone is ambiguous — a model id belongs to one API — so it
    is applied to the PRIMARY producer's own provider, never to whichever provider
    happens to be first in the supervision pool."""
    env = {"OPENROUTER_API_KEY": "1", "DEEPSEEK_API_KEY": "1",
           "SUPERVISOR_MODEL": "custom-distinct"}
    # Assessment primary provider is OpenRouter here (first configured in order).
    assert primary_identity("assessment", env).provider == "openrouter"

    reviewer = supervisor_identity("assessment", env)

    assert reviewer == ModelIdentity("openrouter", "custom-distinct")
    # It is NOT bound to deepseek, whose API would not recognise that model id.
    assert reviewer.provider != "deepseek"


def test_an_explicit_model_equal_to_the_primary_without_a_pin_returns_none():
    """On the primary provider the override is not distinct, and without a pin the
    resolver refuses rather than binding the model to some other provider."""
    env = {"OPENROUTER_API_KEY": "1", "DEEPSEEK_API_KEY": "1"}
    env["SUPERVISOR_MODEL"] = provider_model("openrouter", env)  # == the primary model

    assert supervisor_identity("assessment", env) is None


# ------------------------------------------ whole-chain provider exclusion (issue 2)

def test_a_reviewer_is_never_drawn_from_an_excluded_provider_chain():
    """Assessment falls back across its whole configured chain, so any of those
    providers may have produced a row. A reviewer drawn from the chain could be
    re-reviewing its own output; the conservative answer is no supervisor."""
    from engine.llm_clients import capability_providers

    env = {"DOUBLEWORD_API_KEY": "1", "OPENROUTER_API_KEY": "1"}
    chain = capability_providers("assessment", env)
    assert set(chain) == {"doubleword", "openrouter"}

    # openrouter is the only distinct supervision provider, but it is itself a
    # possible producer, so excluding the chain leaves no honest reviewer.
    assert supervisor_identity("assessment", env, exclude_providers=chain) is None

    # A provider outside the chain may still serve.
    env["MOONSHOT_API_KEY"] = "1"
    reviewer = supervisor_identity(
        "assessment", env,
        exclude_providers=capability_providers("assessment", env))
    assert reviewer is not None
    assert reviewer.provider == "moonshot"


# ------------------------------------------------------------ the bounded call

class _Recording:
    """A stand-in chat client that records exactly one completion request."""

    def __init__(self, content):
        self._content = content
        self.calls = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=self._content))])


def _distinct_env():
    return {"DEEPSEEK_API_KEY": "sk-primary", "MOONSHOT_API_KEY": "sk-reviewer"}


def _request():
    return supervisor.SupervisionRequest(
        task="unit",
        contract="Return the token OK.",
        violations=["the artifact was absent"],
        artifact="prose that is not the artifact",
        context="conversation and findings",
    )


def test_a_correction_is_one_toolless_temperature_zero_call(monkeypatch):
    client = _Recording("OK")
    monkeypatch.setattr("engine.llm_clients.chat_client", lambda provider, env: client)

    outcome = supervisor.supervise(
        _request(), primary_capability="orchestration",
        validate=lambda raw: raw.strip() or None, env=_distinct_env())

    assert outcome.status == "corrected"
    assert outcome.independent is True
    assert len(client.calls) == 1, "exactly one attempt, no retries"
    call = client.calls[0]
    assert call["temperature"] == 0
    assert "tools" not in call, "the supervisor is offered no tools"
    assert call["model"] == "deepseek-v4-flash"


def test_an_invalid_correction_is_rejected_without_a_retry(monkeypatch):
    client = _Recording("not a valid artifact")
    monkeypatch.setattr("engine.llm_clients.chat_client", lambda provider, env: client)

    outcome = supervisor.supervise(
        _request(), primary_capability="orchestration",
        validate=lambda raw: None, env=_distinct_env())

    assert outcome.status == "invalid"
    assert outcome.parsed is None
    assert len(client.calls) == 1, "a rejected reply is not re-asked"


def test_no_distinct_supervisor_makes_no_call(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("nothing should be called without a distinct reviewer")

    monkeypatch.setattr("engine.llm_clients.chat_client", explode)

    outcome = supervisor.supervise(
        _request(), primary_capability="orchestration",
        validate=lambda raw: raw, env={"DEEPSEEK_API_KEY": "sk-only"})

    assert outcome.status == "no_supervisor"
    assert outcome.parsed is None


def test_a_provider_failure_erases_no_evidence(monkeypatch):
    def dead(provider, env):
        raise RuntimeError("provider down")

    monkeypatch.setattr("engine.llm_clients.chat_client", dead)

    outcome = supervisor.supervise(
        _request(), primary_capability="orchestration",
        validate=lambda raw: raw, env=_distinct_env())

    assert outcome.status == "unavailable"
    assert outcome.parsed is None
    # The failure is nameable but blames nothing on the artifact.
    assert "RuntimeError" in outcome.detail


def test_the_trace_is_bounded_and_redacted(monkeypatch):
    secret = "sk-super-secret-token"
    client = _Recording(f"leaked {secret} " + "x" * (supervisor.MAX_TRACE_CHARS + 500))
    monkeypatch.setattr("engine.llm_clients.chat_client", lambda provider, env: client)

    def redact(value):
        return str(value).replace(secret, "[redacted]")

    outcome = supervisor.supervise(
        _request(), primary_capability="orchestration",
        validate=lambda raw: raw.strip() or None, env=_distinct_env(), redact=redact)

    assert secret not in outcome.raw
    assert len(outcome.raw) <= supervisor.MAX_TRACE_CHARS
    trace = supervisor.trace_artifact(_request(), outcome)
    assert trace["status"] == "ok"
    assert trace["args_summary"] == "deepseek/deepseek-v4-flash"


def test_the_trace_never_claims_an_independence_that_did_not_happen():
    outcome = supervisor.SupervisionOutcome(
        status="no_supervisor", detail="no distinct supervisor model is configured")
    trace = supervisor.trace_artifact(_request(), outcome)

    assert trace["status"] == "error"
    assert trace["args_summary"] == "no distinct supervisor"


# ----------------------------------------------------- intake spec recovery

def _spec_text():
    spec = {
        "benchmark_type": "tool_assessment",
        "category": "RAG platforms",
        "objective": "RAG over internal documents",
        "candidates": [
            {"name": "alpha", "display_name": "Alpha",
             "docs_url": "https://example.com/alpha", "kind": "saas"},
            {"name": "beta_lib", "display_name": "Beta Lib",
             "docs_url": "https://example.com/beta", "kind": "local_tool",
             "role": "build_component"},
        ],
    }
    return f"Here is the spec.\n```json\n{json.dumps(spec)}\n```"


class _ProseThenSearch:
    """Searches once when given tools, then returns prose with no spec — the bug."""

    def __init__(self):
        self.calls = []

    def __call__(self, env=None, **kwargs):
        self.calls.append(kwargs)
        if "tools" in kwargs and len(self.calls) == 1:
            return _response(_msg(tool_calls=[_tool_call()]))
        return _response(_msg(content="Here is a shortlist of what I found."))


def _msg(content=None, tool_calls=None):
    m = types.SimpleNamespace(content=content, tool_calls=tool_calls or [])
    m.model_dump = lambda exclude_none=False: {"role": "assistant", "content": content}
    return m


def _response(message):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _tool_call():
    return types.SimpleNamespace(
        id="c1", function=types.SimpleNamespace(
            name="web_search", arguments=json.dumps({"query": "rag platforms"})))


def _agent(monkeypatch, recorder, tmp_path):
    monkeypatch.setattr(agent_mod, "_orchestrator_complete", recorder)
    monkeypatch.setattr(
        agent_mod, "dispatch_tool",
        lambda name, args, ctx: json.dumps({"ok": True, "results": []}))
    emitted = []
    a = agent_mod.Orchestrator(
        run_id="run-recover", run_dir=str(tmp_path / "run"),
        emit=lambda kind, payload: emitted.append((kind, payload)))
    a.emitted = emitted
    a.deltas = []
    a.states = []
    a._delta = lambda text: a.deltas.append(text)
    a._state = lambda phase, candidates=None: a.states.append(phase)
    a._check_cancelled = lambda: None
    a._prepare_brief = lambda _message: ("", None)
    # Gates beyond the recovery itself are exercised in their own suites.
    a._ensure_discovery_reach = lambda spec: spec
    a._ensure_build_path = lambda spec: spec
    a._review_shortlist = lambda spec: spec
    return a


def test_researched_prose_is_recovered_into_a_spec_by_the_supervisor(monkeypatch, tmp_path):
    """The critical bug: prose after research becomes a real spec, not a dead end."""
    recorder = _ProseThenSearch()
    a = _agent(monkeypatch, recorder, tmp_path)
    # A distinct supervisor is configured, and it emits the fenced spec.
    a.runtime_env = _distinct_env()
    client = _Recording(_spec_text())
    monkeypatch.setattr("engine.llm_clients.chat_client", lambda provider, env: client)

    a.chat("what platform for RAG over our documents")

    assert any(kind == "artifact" and payload.get("kind") == "spec"
               for kind, payload in a.emitted)
    assert "SPEC_CONFIRM" in a.states
    # The supervisor was asked exactly once.
    assert len(client.calls) == 1
    trace = next(payload for kind, payload in a.emitted
                 if kind == "artifact" and payload.get("tool") == "supervisor:spec_recovery")
    assert trace["status"] == "ok"
    reviewer = ModelIdentity("deepseek", provider_model("deepseek", a.runtime_env))
    # The recovered spec is authored by the supervisor. A later shortlist
    # review must exclude that reviewer too, rather than letting it judge its
    # own recovered artifact.
    assert reviewer in a._orchestration_producers
    assert a._last_orchestration_producer == reviewer


def test_without_a_supervisor_the_prose_is_not_passed_off_as_a_benchmark(monkeypatch, tmp_path):
    recorder = _ProseThenSearch()
    a = _agent(monkeypatch, recorder, tmp_path)
    a.runtime_env = {"DEEPSEEK_API_KEY": "sk-only"}  # no distinct reviewer

    a.chat("what platform for RAG over our documents")

    assert not any(kind == "artifact" and payload.get("kind") == "spec"
                   for kind, payload in a.emitted)
    assert "SPEC_CONFIRM" not in a.states
    joined = " ".join(a.deltas)
    assert "did not produce a runnable benchmark spec" in joined
    assert "SUPERVISOR_PROVIDER" in joined
