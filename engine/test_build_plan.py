"""A parts list is not an implementation plan.

When no marketed product met the requirement the report named three assessed
libraries with their install commands and stopped. That leaves the reader to
work out the design themselves: which piece does what, in what order, where the
output goes, and how any of it reaches the system they already run.

These cover the plan that replaces the inventory — and the two rules that keep
it honest: it is only offered when the scores already say building is the
answer, and it never becomes the reason to build.
"""
from __future__ import annotations

import json
import types

from engine import build_plan
from engine.tool_assessment import build_path_is_the_answer, write_assessment_report

COMPONENTS = [
    ("sympy", {"display_name": "SymPy", "rating": 88, "role": "build_component",
               "implementable": True, "build_commands": ["pip install sympy"],
               "evidence": ["Symbolic algebra and equation generation"]}),
    ("matplotlib", {"display_name": "Matplotlib", "rating": 91, "role": "build_component",
                    "implementable": True, "build_commands": ["pip install matplotlib"],
                    "evidence": ["Renders plots to SVG and PNG"]}),
]

PLAN_JSON = json.dumps({
    "summary": "Generate the question symbolically, render its diagram to SVG, and emit both "
               "as one payload.",
    "stack": ["Python 3.11", "FastAPI"],
    "components": [
        {"name": "SymPy", "role": "generates the question and its answer symbolically"},
        {"name": "Matplotlib", "role": "renders the accompanying diagram to SVG"},
    ],
    "steps": [
        "Model each question type as a SymPy template with randomised parameters.",
        "Render the figure with Matplotlib and serialise it to inline SVG.",
        "Expose a POST /questions endpoint returning question, answer and diagram.",
    ],
    "integration": "LearningAnts calls the endpoint and stores the returned SVG with the item.",
    "risks": ["Diagram styling has to be matched to the existing item renderer."],
})


def test_plan_prompt_does_not_turn_unassessed_infrastructure_into_evidence():
    prompt = build_plan.BUILD_PLAN_SYSTEM

    assert "Do not claim that an unassessed service" in prompt
    assert "Only the supplied component evidence is measured" in prompt


def _complete(reply):
    def call(env=None, **kwargs):
        call.kwargs = kwargs
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))])
    return call


# ------------------------------------------------------------------ when to plan

def test_a_plan_is_offered_when_every_product_failed_and_a_component_did_not():
    metrics = {
        "ace_quiz": {"rating": 49, "implementable": False, "role": "product"},
        "sympy": {"rating": 88, "implementable": True, "role": "build_component"},
    }
    assert [name for name, _ in build_path_is_the_answer(metrics)] == ["sympy"]


def test_no_plan_while_a_product_still_works():
    """Advice to build, beside something you can buy that does the job, is advice
    the evidence does not support."""
    metrics = {
        "good_product": {"rating": 80, "implementable": True, "role": "product"},
        "sympy": {"rating": 88, "implementable": True, "role": "build_component"},
    }
    assert build_path_is_the_answer(metrics) == []


def test_no_plan_when_the_components_failed_too():
    metrics = {
        "ace_quiz": {"rating": 49, "implementable": False, "role": "product"},
        "sympy": {"rating": 20, "implementable": False, "role": "build_component"},
    }
    assert build_path_is_the_answer(metrics) == []


def test_no_plan_for_a_field_of_components_only():
    """With nothing marketed assessed, "no product works" was never established."""
    metrics = {"sympy": {"rating": 88, "implementable": True, "role": "build_component"}}
    assert build_path_is_the_answer(metrics) == []


# ------------------------------------------------------------------- the plan

def test_the_plan_is_an_architecture_not_a_parts_list():
    plan = build_plan.generate("math questions with diagrams", {"stack": ["LearningAnts"]},
                               COMPONENTS, complete=_complete(PLAN_JSON))

    assert plan["stack"] == ["Python 3.11", "FastAPI"]
    # Each part is described by what it does HERE, not by what it is generally.
    assert plan["components"][0]["role"].startswith("generates the question")
    assert len(plan["steps"]) == 3
    assert "LearningAnts" in plan["integration"]


def test_the_assessed_evidence_is_what_the_plan_is_designed_over():
    call = _complete(PLAN_JSON)
    build_plan.generate("math questions", {}, COMPONENTS, complete=call)

    sent = call.kwargs["messages"][-1]["content"]
    assert "Renders plots to SVG" in sent, "the plan must see what was actually assessed"
    assert "pip install sympy" in sent


def test_a_role_for_something_never_assessed_is_dropped():
    """A recommendation the run cannot stand behind does not get printed."""
    reply = json.loads(PLAN_JSON)
    reply["components"].append({"name": "SomeUnassessedThing", "role": "does magic"})
    plan = build_plan.generate("q", {}, COMPONENTS, complete=_complete(json.dumps(reply)))

    assert [c["name"] for c in plan["components"]] == ["SymPy", "Matplotlib"]


def test_a_plan_with_no_steps_is_no_plan():
    """An empty scaffold under a confident heading is worse than no section."""
    reply = json.loads(PLAN_JSON)
    reply["steps"] = []
    assert build_plan.generate("q", {}, COMPONENTS, complete=_complete(json.dumps(reply))) is None


def test_an_unparseable_or_failed_reply_never_raises():
    assert build_plan.generate("q", {}, COMPONENTS, complete=_complete("not json")) is None

    def boom(env=None, **kwargs):
        raise RuntimeError("provider down")

    assert build_plan.generate("q", {}, COMPONENTS, complete=boom) is None


def _complete_sequence(*replies):
    """One reply per call, so a repaired second attempt can differ from the first."""
    def call(env=None, **kwargs):
        call.calls.append(kwargs)
        reply = replies[min(len(call.calls) - 1, len(replies) - 1)]
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))])
    call.calls = []
    return call


def test_a_malformed_reply_is_repaired_once_with_the_reason():
    """The retry sees the bad reply and why it failed, so it can correct rather
    than repeat — the same feedback shape the assessment retry uses."""
    call = _complete_sequence("not json", PLAN_JSON)
    plan = build_plan.generate("q", {}, COMPONENTS, complete=call)

    assert plan is not None and len(plan["steps"]) == 3
    assert len(call.calls) == 2
    repair = call.calls[1]["messages"]
    assert repair[-2] == {"role": "assistant", "content": "not json"}
    assert "failed validation" in repair[-1]["content"]
    assert "not valid JSON" in repair[-1]["content"]


def test_an_empty_first_reply_is_not_replayed_as_an_assistant_turn():
    """Anthropic-style backends reject an assistant turn with empty content, so
    replaying a blank reply would doom the repair round against every provider.
    The retry gets only the reason; the plan is still recovered."""
    for blank in (None, "", "   "):
        call = _complete_sequence(blank, PLAN_JSON)
        plan = build_plan.generate("q", {}, COMPONENTS, complete=call)

        assert plan is not None and len(plan["steps"]) == 3
        assert len(call.calls) == 2
        repair = call.calls[1]["messages"]
        assert all(turn["role"] != "assistant" for turn in repair)
        assert "failed validation" in repair[-1]["content"]


def test_repair_is_one_round_and_the_contract_stays_fail_open():
    """A plan that retries until it parses is not the optional stage it claims
    to be. Two attempts, then None."""
    call = _complete_sequence("not json")
    assert build_plan.generate("q", {}, COMPONENTS, complete=call) is None
    assert len(call.calls) == 2


def test_the_failure_detail_tells_a_dead_provider_from_a_bad_reply():
    """The trace should say WHICH failure swallowed the plan: a provider that
    never answered is a different problem from a model that cannot make the shape."""
    def boom(env=None, **kwargs):
        raise RuntimeError("provider down")

    failure = {}
    assert build_plan.generate("q", {}, COMPONENTS, complete=boom, failure=failure) is None
    assert failure["detail"] == "provider call failed"

    failure = {}
    assert build_plan.generate("q", {}, COMPONENTS,
                               complete=_complete("not json"), failure=failure) is None
    assert failure["detail"].startswith("reply unparseable after repair")
    assert "not valid JSON" in failure["detail"]


def test_no_components_means_no_completion_is_spent():
    call = _complete(PLAN_JSON)
    assert build_plan.generate("q", {}, [], complete=call) is None
    assert not hasattr(call, "kwargs")


# ------------------------------------------------------------------ the report

def test_the_report_carries_the_plan(tmp_path):
    metrics = {
        "ace_quiz": {"rating": 49, "implementable": False, "role": "product",
                     "reason": "no diagrams", "evidence": [], "execution_mode": "comparison_only"},
        "sympy": {"rating": 88, "implementable": True, "role": "build_component",
                  "display_name": "SymPy", "reason": "ok", "evidence": [],
                  "execution_mode": "sandbox_verifiable"},
    }
    plan = build_plan.generate("q", {}, COMPONENTS, complete=_complete(PLAN_JSON))
    markdown = write_assessment_report(metrics, [], str(tmp_path / "r.md"), build_plan=plan)

    assert "## How to build this yourself" in markdown
    assert "**Stack:** Python 3.11, FastAPI" in markdown
    assert "renders the accompanying diagram to SVG" in markdown
    # The plan IS the build path: score and install line ride with the part.
    assert ("- **SymPy** (88/100, `pip install sympy`) — generates the question "
            "and its answer symbolically") in markdown
    assert "## Build path" not in markdown, "one plan, not a plan beside an inventory"
    assert "1. Model each question type" in markdown
    assert "LearningAnts calls the endpoint" in markdown
    assert "Diagram styling has to be matched" in markdown


def test_a_report_without_a_plan_is_unchanged(tmp_path):
    metrics = {"a": {"rating": 70, "implementable": True, "role": "product",
                     "reason": "ok", "evidence": [], "execution_mode": "comparison_only"}}
    markdown = write_assessment_report(metrics, [], str(tmp_path / "r.md"))

    assert "How to build this yourself" not in markdown


# ------------------------------------------------- components are not products

BOTH = {
    "matplotlib": {"rating": 100, "implementable": True, "role": "build_component",
                   "display_name": "Matplotlib", "reason": "great docs", "evidence": [],
                   "execution_mode": "sandbox_verifiable", "build_commands": ["pip install matplotlib"]},
    "sympy": {"rating": 100, "implementable": True, "role": "build_component",
              "display_name": "SymPy", "reason": "great docs", "evidence": [],
              "execution_mode": "sandbox_verifiable", "build_commands": ["pip install sympy"]},
    "creately": {"rating": 49, "implementable": False, "role": "product",
                 "display_name": "Creately", "reason": "no question generation", "evidence": [],
                 "execution_mode": "comparison_only"},
    "edraw": {"rating": 19, "implementable": False, "role": "product",
              "display_name": "Edraw", "reason": "no question generation", "evidence": [],
              "execution_mode": "comparison_only"},
}


def test_a_library_never_takes_first_place_from_the_products(tmp_path):
    """Asked what generates math questions, the table answered "1. Matplotlib"."""
    markdown = write_assessment_report(BOTH, [], str(tmp_path / "r.md"))
    ranked = [line for line in markdown.splitlines() if line.startswith("| 1 |")]

    assert ranked and "Creately" in ranked[0]
    assert "| 1 | Matplotlib" not in markdown
    assert "| 2 | SymPy" not in markdown


def test_components_are_absent_from_the_ranked_table_entirely(tmp_path):
    markdown = write_assessment_report(BOTH, [], str(tmp_path / "r.md"))
    table = markdown.split("## How to build this yourself")[0]

    assert "Matplotlib" not in table and "SymPy" not in table


def test_the_plan_replaces_the_parts_list_entirely(tmp_path):
    """Two sections naming the same libraries read as two rival recommendations."""
    plan = build_plan.generate("q", {}, COMPONENTS, complete=_complete(PLAN_JSON))
    markdown = write_assessment_report(BOTH, [], str(tmp_path / "r.md"), build_plan=plan)

    assert "## Build path" not in markdown
    assert markdown.count("## How to build this yourself") == 1
    assert "generates the question and its answer symbolically" in markdown
    assert "could not be generated on this run" not in markdown


# ------------------------------------------------------- when the plan is lost

def test_a_lost_plan_still_leaves_the_reader_the_parts(tmp_path):
    """The one place a bare component list is still allowed, and it says why."""
    markdown = write_assessment_report(BOTH, [], str(tmp_path / "r.md"))
    section = markdown.split("## How to build this yourself")[1].split("\n## ")[0]

    assert "could not be generated on this run" in section
    assert "- **Matplotlib** — 100/100; documented setup: `pip install matplotlib`" in section
    assert "SymPy" in section


def test_an_unassessed_component_is_not_offered_as_a_building_block(tmp_path):
    """A part nothing is known about is not a part anyone can build with."""
    metrics = {**BOTH, "geogebra": {"rating": None, "implementable": None,
                                    "role": "build_component", "display_name": "GeoGebra API",
                                    "reason": "scrape failed", "evidence": [],
                                    "execution_mode": "comparison_only"}}
    markdown = write_assessment_report(metrics, [], str(tmp_path / "r.md"))

    assert "GeoGebra" not in markdown.split("## Findings")[0]


def test_no_fallback_section_while_a_product_still_works(tmp_path):
    """Nothing to build for: the verdict never said building was the answer."""
    metrics = {"alpha": {"rating": 70, "implementable": True, "role": "product",
                         "reason": "Fine.", "evidence": [], "execution_mode": "comparison_only"},
               "sympy": {"rating": 88, "implementable": True, "role": "build_component",
                         "display_name": "SymPy", "reason": "ok", "evidence": [],
                         "execution_mode": "comparison_only"}}

    assert "How to build this yourself" not in write_assessment_report(
        metrics, [], str(tmp_path / "r.md"))


def test_the_pdf_ranks_products_only():
    from engine.pdf_report import _ranked
    assert [name for name, _ in _ranked(BOTH, True)] == ["creately", "edraw"]
