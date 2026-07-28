"""The discovery sweep has to reach the pools rule 3a names, not just be asked to.

INTAKE_SYSTEM rule 3a asks the intake model for two query shapes: one rung up the
category ladder, and an app-store channel query when an individual end user is a
plausible audience. On a live run the model ran the ladder query, silently skipped
the channel query, and dropped the objective's distinguishing requirement term from
most of the sweep. The channel + ladder + requirement-terms combination is the only
dialect that surfaced a known store-first consumer product, and it came back at
position 1 — so what the sweep missed was not a marginal name.

Prompt instructions are honoured probabilistically. The remedy this repo already
uses is a bounded code gate: one focused completion, deterministic execution,
fail-open, a trace artifact. _ensure_discovery_reach is that gate, and these are
its tests.
"""
from __future__ import annotations

import json
import types

import pytest

from engine import agent as agent_mod


PRODUCTS = {
    "benchmark_type": "tool_assessment",
    "category": "Practice question generation",
    "objective": "generate practice questions with diagrams",
    "constraints": {"must_have": ["diagrams"]},
    "candidates": [
        {"name": "alpha_tool", "display_name": "Alpha Tool",
         "docs_url": "https://alpha.test/docs", "kind": "saas", "role": "product"},
    ],
}

QUERIES = json.dumps({
    "ladder_query": "STEM learning platform with diagram support",
    "channel_query": "diagram practice question app iphone",
})

RESULTS = [
    {"title": "Bravo", "url": "https://bravo.test/docs"},
    {"title": "Charlie", "url": "https://charlie.test/docs"},
]

HARVEST = json.dumps({"candidates": [
    {"name": "bravo", "display_name": "Bravo", "docs_url": "https://bravo.test/docs",
     "kind": "saas"},
]})


class _Model:
    """Answers the reach call then the harvest call, recording both."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, env=None, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))])


class _Search:
    def __init__(self, results=None, error=None):
        self.results = RESULTS if results is None else results
        self.error = error
        self.queries = []

    def __call__(self, query, n=5, env=None):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return list(self.results)


def _agent(monkeypatch, tmp_path, model, search):
    monkeypatch.setattr(agent_mod, "_orchestrator_complete", model)
    monkeypatch.setattr("engine.docs_intel.web_search", search)
    a = agent_mod.Orchestrator("run-1", str(tmp_path), emit=lambda kind, payload: None)
    a.emitted = []
    a.emit = lambda kind, payload: a.emitted.append((kind, payload))
    return a


def _trace(a):
    return [p for kind, p in a.emitted
            if kind == "artifact" and p.get("tool") == "discovery_reach"]


# ------------------------------------------------------- the two missing shapes

def test_the_gate_runs_both_queries_when_the_turn_ran_neither(monkeypatch, tmp_path):
    """The measured failure: the model skipped the channel query entirely."""
    model, search = _Model(QUERIES, HARVEST), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    a._ensure_discovery_reach(dict(PRODUCTS))

    assert search.queries == ["STEM learning platform with diagram support",
                              "diagram practice question app iphone"]


def test_both_gate_queries_run_even_when_the_sweep_ran_a_channel_shape(monkeypatch, tmp_path):
    """Shape-matching a prior query is not grounds to skip.

    Measured: intake ran its own channel-shaped query with the requirement
    terms dropped ("math practice app ios"), which returned kids-app listicles
    — and the gate then skipped its OWN channel query, the only one required to
    keep those terms. The gate can judge a prior query's shape but never its
    quality, so both of its queries run unconditionally.
    """
    model, search = _Model(QUERIES, HARVEST), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)
    a._turn_search_queries = ["practice question app iphone"]

    a._ensure_discovery_reach(dict(PRODUCTS))

    assert search.queries == ["STEM learning platform with diagram support",
                              "diagram practice question app iphone"]


@pytest.mark.parametrize("query", [
    "diagram question app iphone",
    "practice app android",
    "homework helper app app store",
    "diagram question bank app google play",
])
def test_channel_shapes_are_recognised(query):
    assert agent_mod._is_channel_query(query)


def test_the_turn_query_list_resets_between_chat_calls(monkeypatch, tmp_path):
    """Per turn, because the gate asks what THIS turn's sweep covered."""
    a = agent_mod.Orchestrator("run-1", str(tmp_path), emit=lambda kind, payload: None)

    assert a._turn_search_queries == []


# ------------------------------------------------------------- what it may add

def test_a_harvested_candidate_joins_the_shortlist(monkeypatch, tmp_path):
    model, search = _Model(QUERIES, HARVEST), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(dict(PRODUCTS))

    assert [c["name"] for c in spec["candidates"]] == ["alpha_tool", "bravo"]
    assert spec["candidates"][1]["role"] == "product"
    assert _trace(a)[0]["detail"] == "ran 2 supplementary searches; added Bravo"


def test_a_docs_url_not_among_the_results_is_rejected(monkeypatch, tmp_path):
    """A URL the model wrote from memory is what this check exists to catch."""
    invented = json.dumps({"candidates": [
        {"name": "delta", "display_name": "Delta", "docs_url": "https://delta.test/docs",
         "kind": "saas"},
    ]})
    model, search = _Model(QUERIES, invented), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(dict(PRODUCTS))

    assert spec["candidates"] == PRODUCTS["candidates"]
    assert _trace(a)[0]["detail"].endswith("added none")


def test_at_most_three_candidates_are_added(monkeypatch, tmp_path):
    results = [{"title": f"n{i}", "url": f"https://n{i}.test/docs"} for i in range(6)]
    harvest = json.dumps({"candidates": [
        {"name": f"n{i}", "display_name": f"N{i}", "docs_url": f"https://n{i}.test/docs",
         "kind": "saas"} for i in range(6)
    ]})
    model, search = _Model(QUERIES, harvest), _Search(results)
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(dict(PRODUCTS))

    assert len(spec["candidates"]) == 4


def test_a_name_already_shortlisted_is_never_added_twice(monkeypatch, tmp_path):
    results = [{"title": "Alpha Tool", "url": "https://alpha.test/other"}, *RESULTS]
    harvest = json.dumps({"candidates": [
        {"name": "alpha_tool", "display_name": "Alpha Tool",
         "docs_url": "https://alpha.test/other", "kind": "saas"},
        {"name": "bravo", "display_name": "Bravo", "docs_url": "https://bravo.test/docs",
         "kind": "saas"},
    ]})
    model, search = _Model(QUERIES, harvest), _Search(results)
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(dict(PRODUCTS))

    assert [c["name"] for c in spec["candidates"]] == ["alpha_tool", "bravo"]


def test_an_excluded_name_is_not_reintroduced(monkeypatch, tmp_path):
    spec_in = dict(PRODUCTS)
    spec_in["excluded"] = [{"name": "bravo", "display_name": "Bravo",
                            "kind": "violation", "violates": "no diagrams"}]
    model, search = _Model(QUERIES, HARVEST), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(spec_in)

    assert [c["name"] for c in spec["candidates"]] == ["alpha_tool"]


def test_the_shortlist_never_exceeds_the_schema_cap(monkeypatch, tmp_path):
    spec_in = dict(PRODUCTS)
    spec_in["candidates"] = [
        {"name": f"c{i}", "display_name": f"C{i}", "docs_url": f"https://c{i}.test/",
         "kind": "saas", "role": "product"} for i in range(20)
    ]
    model, search = _Model(QUERIES, HARVEST), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(spec_in)

    assert len(spec["candidates"]) == 20


def test_results_persist_as_findings_for_the_next_turn(monkeypatch, tmp_path):
    model, search = _Model(QUERIES, HARVEST), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    a._ensure_discovery_reach(dict(PRODUCTS))

    assert {f["url"] for f in a.findings} == {r["url"] for r in RESULTS}


# ------------------------------------------------------------------- fail open

def test_a_dead_provider_never_costs_the_user_the_spec(monkeypatch, tmp_path):
    model, search = _Model(RuntimeError("provider down")), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(dict(PRODUCTS))

    assert spec["candidates"] == PRODUCTS["candidates"]
    assert search.queries == []
    assert _trace(a)[0]["status"] == "error"


def test_an_unparseable_query_reply_leaves_the_spec_alone(monkeypatch, tmp_path):
    model, search = _Model("sure, here are some ideas"), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(dict(PRODUCTS))

    assert spec["candidates"] == PRODUCTS["candidates"]
    assert _trace(a)[0]["status"] == "error"


def test_an_empty_query_string_is_a_parse_failure():
    with pytest.raises(ValueError):
        agent_mod._parse_discovery_queries(json.dumps(
            {"ladder_query": "", "channel_query": "x app iphone"}))


def test_a_failed_search_skips_rather_than_raising(monkeypatch, tmp_path):
    model, search = _Model(QUERIES, HARVEST), _Search(error=RuntimeError("SERP down"))
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(dict(PRODUCTS))

    assert spec["candidates"] == PRODUCTS["candidates"]
    assert _trace(a)[0] == {
        "kind": "trace", "tool": "discovery_reach", "args_summary": "1 candidates",
        "status": "ok", "detail": "ran 0 supplementary searches; added none",
    }


def test_an_unparseable_harvest_reply_leaves_the_spec_alone(monkeypatch, tmp_path):
    model, search = _Model(QUERIES, "no JSON here"), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)

    spec = a._ensure_discovery_reach(dict(PRODUCTS))

    assert spec["candidates"] == PRODUCTS["candidates"]
    assert _trace(a)[0]["status"] == "error"


def test_an_extraction_spec_is_left_alone(monkeypatch, tmp_path):
    model, search = _Model(QUERIES, HARVEST), _Search()
    a = _agent(monkeypatch, tmp_path, model, search)
    spec = {"benchmark_type": "extraction", "candidates": []}

    assert a._ensure_discovery_reach(spec) is spec
    assert model.calls == []


# ---------------------------------------------------------------- the ordering

def test_the_reach_gate_runs_before_the_shortlist_review(monkeypatch, tmp_path):
    """Anything the gate adds has to be on the shortlist when review culls it.

    Otherwise a candidate this gate found would enter the spec unreviewed, and a
    constraint violation the review exists to catch would ship into the run.
    """
    from engine.test_intake_loop import _Recorder, _make_agent

    a = _make_agent(monkeypatch, _Recorder(spec_on_toolless=True), tmp_path)
    order = []
    a._ensure_discovery_reach = lambda spec: order.append("reach") or spec
    a._ensure_build_path = lambda spec: order.append("build") or spec
    a._review_shortlist = lambda spec: order.append("review") or spec

    a.chat("what tool for generating practice questions")

    assert order == ["reach", "build", "review"]


def test_the_gate_prompts_name_no_products():
    """Same discipline the intake prompt is held to: shapes, never names.

    A query built around one product's name draws that name and its imitators,
    which is the pool the sweep already had.
    """
    assert "Never name a specific product in either query" in agent_mod.DISCOVERY_REACH_SYSTEM
    assert "MUST be copied exactly from the supplied results" in agent_mod.DISCOVERY_HARVEST_SYSTEM


def test_the_reach_prompt_keeps_the_requirement_terms_rule():
    """The other half of the measured failure: the terms were dropped."""
    prompt = " ".join(agent_mod.DISCOVERY_REACH_SYSTEM.split())

    assert "MUST keep the objective's distinguishing requirement terms" in prompt
    assert "ONE RUNG UP the category ladder" in prompt
    assert "app iphone" in prompt
