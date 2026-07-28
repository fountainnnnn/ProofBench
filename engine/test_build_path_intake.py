"""A run that finds nothing must still leave the user with somewhere to go.

Asked for math question generators that support diagrams, intake shortlisted
five marketed products and no building blocks. Every product was then rated not
implementable for the same reason — no diagram support — so the run concluded
"no candidate met the requirements" and stopped there.

Everything downstream of intake already handled this: candidates carry a role,
the verdict concludes self-implementation when products fail and a component
clears the bar, and the report prints one plan built out of those components.
None of it could ever fire, because the rule that produces components
was conditioned on intake knowing something only the assessment stage can
determine — whether a product actually satisfies the requirement.
"""
from __future__ import annotations

from engine.agent import _normalize_intake_spec, intake_system


def flat(dataset_available=False) -> str:
    """The prompt with its line wrapping removed.

    Asserting on raw text makes a test fail when a sentence is merely rewrapped,
    which says nothing about the rule it encodes.
    """
    return " ".join(intake_system(dataset_available).split())


def test_components_are_always_shortlisted():
    """Unconditional, because every condition tried so far failed to fire.

    Keying it to "the user stated a must-have" did not work: re-running the
    original question, intake left constraints.must_have empty even though the
    user asked for diagram support in the first sentence, so the rule never
    triggered and the spec came back with three products and no components.
    """
    prompt = flat()

    assert "ALWAYS shortlist 1-3 documented building blocks" in prompt
    assert "Every tool_assessment spec must contain at least one" in prompt
    assert "Do NOT make this depend on the user stating a must-have" in prompt


def test_the_prompt_says_why_it_is_unconditional():
    """The rule is easy to 'optimise' back into a conditional without this."""
    prompt = flat()

    assert "you cannot know whether any product satisfies" in prompt
    assert "no way to add one" in prompt


def test_building_is_still_never_asserted_by_intake():
    """The verdict decides from measured scores; intake only supplies options."""
    prompt = flat()

    assert "You do not decide that building is the better answer" in prompt
    assert "keep the products you found on the shortlist too" in prompt


def test_the_extraction_variant_keeps_the_same_rule():
    assert "ALWAYS shortlist 1-3 documented building blocks" in flat(dataset_available=True)


def test_a_component_role_survives_spec_normalization():
    """The role has to reach the server, or the verdict cannot see the build path."""
    spec = _normalize_intake_spec({
        "benchmark_type": "tool_assessment",
        "category": "Math question generation",
        "candidates": [
            {"name": "ace_quiz", "display_name": "Ace Quiz",
             "docs_url": "https://acequiz.ai/docs", "kind": "saas"},
            {"name": "mathjax", "display_name": "MathJax",
             "docs_url": "https://docs.mathjax.org/", "kind": "local_tool",
             "role": "build_component"},
        ],
    }, dataset_available=False)

    roles = {c["name"]: c["role"] for c in spec["candidates"]}
    assert roles == {"ace_quiz": "product", "mathjax": "build_component"}


def test_an_unrecognised_role_falls_back_to_product():
    """The build path is claimed explicitly, never inferred from a typo."""
    spec = _normalize_intake_spec({
        "benchmark_type": "tool_assessment",
        "category": "c",
        "candidates": [{"name": "a", "display_name": "A",
                        "docs_url": "https://a.example.com/docs", "kind": "saas",
                        "role": "library"}],
    }, dataset_available=False)

    assert spec["candidates"][0]["role"] == "product"


# --------------------------------------------------------------- the enforcement

import json
import types

import pytest

from engine import agent as agent_mod


def _agent(monkeypatch, tmp_path, reply):
    def complete(env=None, **kwargs):
        complete.messages = kwargs.get("messages")
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))])

    monkeypatch.setattr(agent_mod, "_orchestrator_complete", complete)
    a = agent_mod.Orchestrator("run-1", str(tmp_path), emit=lambda kind, payload: None)
    a.emitted = []
    a.emit = lambda kind, payload: a.emitted.append((kind, payload))
    a._complete = complete
    return a


PRODUCTS = {
    "benchmark_type": "tool_assessment",
    "category": "Math question generation",
    "objective": "generate practice questions with diagrams",
    "candidates": [
        {"name": "ace_quiz", "display_name": "Ace Quiz",
         "docs_url": "https://acequiz.ai/docs", "kind": "saas", "role": "product"},
    ],
}

GOOD_REPLY = json.dumps({"components": [
    {"name": "mathjax", "display_name": "MathJax",
     "docs_url": "https://docs.mathjax.org/", "kind": "local_tool"},
]})


def test_a_products_only_shortlist_gains_a_build_component(monkeypatch, tmp_path):
    """The prompt asks for one and the model ignores it, twice measured."""
    a = _agent(monkeypatch, tmp_path, GOOD_REPLY)

    spec = a._ensure_build_path(dict(PRODUCTS))

    roles = [c["role"] for c in spec["candidates"]]
    assert roles == ["product", "build_component"]
    assert spec["candidates"][1]["docs_url"] == "https://docs.mathjax.org/"


def test_a_shortlist_that_already_has_one_costs_no_completion(monkeypatch, tmp_path):
    a = _agent(monkeypatch, tmp_path, GOOD_REPLY)
    spec = dict(PRODUCTS)
    spec["candidates"] = [*PRODUCTS["candidates"],
                          {"name": "x", "display_name": "X", "docs_url": "https://x.test/",
                           "kind": "local_tool", "role": "build_component"}]

    assert a._ensure_build_path(spec) is spec
    assert not hasattr(a._complete, "messages"), "no completion should have been spent"


def test_a_component_with_an_unusable_url_is_discarded(monkeypatch, tmp_path):
    """Model-authored, so nothing is repaired — only dropped."""
    a = _agent(monkeypatch, tmp_path, json.dumps({"components": [
        {"name": "bad", "display_name": "Bad", "docs_url": "not-a-url", "kind": "local_tool"},
    ]}))

    assert a._ensure_build_path(dict(PRODUCTS))["candidates"] == PRODUCTS["candidates"]


def test_a_failed_pass_never_loses_the_shortlist(monkeypatch, tmp_path):
    def boom(env=None, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(agent_mod, "_orchestrator_complete", boom)
    a = agent_mod.Orchestrator("run-1", str(tmp_path), emit=lambda kind, payload: None)
    a.emit = lambda kind, payload: None

    assert a._ensure_build_path(dict(PRODUCTS))["candidates"] == PRODUCTS["candidates"]


def test_an_extraction_spec_is_left_alone(monkeypatch, tmp_path):
    a = _agent(monkeypatch, tmp_path, GOOD_REPLY)
    spec = {"benchmark_type": "extraction", "candidates": []}

    assert a._ensure_build_path(spec) is spec


def test_a_hosted_service_never_enters_through_the_component_gate(monkeypatch, tmp_path):
    """Measured on "compare Resend and Postmark for transactional email": the gate
    added Nodemailer (right) but also SendGrid and Mailgun — hosted rivals of the
    two products under comparison — and one of them scored 86, above the winner."""
    a = _agent(monkeypatch, tmp_path, json.dumps({"components": [
        {"name": "sendgrid", "display_name": "SendGrid",
         "docs_url": "https://docs.sendgrid.com/", "kind": "hosted_api"},
        {"name": "mailgun", "display_name": "Mailgun",
         "docs_url": "https://documentation.mailgun.com/", "kind": "saas"},
        {"name": "nodemailer", "display_name": "Nodemailer",
         "docs_url": "https://nodemailer.com/about/", "kind": "local_tool"},
    ]}))

    added = [c for c in a._ensure_build_path(dict(PRODUCTS))["candidates"]
             if c["role"] == "build_component"]
    assert [c["name"] for c in added] == ["nodemailer"]


def test_an_unlabelled_kind_becomes_a_local_tool(monkeypatch, tmp_path):
    """The gate only ever adds library-shaped parts, so the ambiguous case is a
    library. The old coercion read "api" in the string and produced hosted_api."""
    a = _agent(monkeypatch, tmp_path, json.dumps({"components": [
        {"name": "webapi_dom", "display_name": "Web API DOM",
         "docs_url": "https://developer.mozilla.org/docs/Web/API", "kind": "browser_api"},
    ]}))

    added = [c for c in a._ensure_build_path(dict(PRODUCTS))["candidates"]
             if c["role"] == "build_component"]
    assert [c["kind"] for c in added] == ["local_tool"]


def test_at_most_three_components_are_added(monkeypatch, tmp_path):
    a = _agent(monkeypatch, tmp_path, json.dumps({"components": [
        {"name": f"c{i}", "display_name": f"C{i}",
         "docs_url": f"https://c{i}.example.com/docs", "kind": "local_tool"}
        for i in range(9)
    ]}))

    added = [c for c in a._ensure_build_path(dict(PRODUCTS))["candidates"]
             if c["role"] == "build_component"]
    assert len(added) == 3
