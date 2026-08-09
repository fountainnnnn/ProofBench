"""The rows nobody read back.

Every step of an assessment is validated and the finished row never is. Three
escapes this week, all caught by a human:

- a library rated 91 with implementable true whose reason argued the documented
  requirement was absent;
- build components failed "for lacking X, which is a hard requirement", where X
  belongs to another part of the build — exactly what the component rule in the
  assessment prompt forbids;
- reason text asserting "meets all hard requirements" beside implementable
  false.

These pin the last pass: the deterministic checks fire on those measured
examples and stay quiet on the phrasings that only look like them, a
contradiction is re-assessed once by the model rather than patched by code, and
whatever survives is published as a caveat instead of shipped as a number.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from engine import self_check, tool_assessment
from engine.self_check import find_contradictions, repair, run_self_check
from engine.tool_assessment import validate_plan, write_assessment_report


def row(**overrides) -> dict:
    base = {
        "rating": 80,
        "implementable": True,
        "reason": "Documented client, worked examples, clear auth.",
        "evidence": ["documented fact"],
        "role": "product",
        "display_name": "Alpha",
        "verification_status": "not_applicable",
        "daytona_triggered": False,
    }
    base.update(overrides)
    return base


def codes(metrics: dict, name: str = "alpha") -> list[str]:
    return [flag["code"] for flag in find_contradictions(metrics) if flag["name"] == name]


# ------------------------------------------------------------ the five checks

def test_a_passing_row_whose_reason_says_the_capability_is_missing_is_flagged():
    """The 91/implementable-true escape: the prose failed the tool, the number did not."""
    metrics = {"alpha": row(rating=91, implementable=True, reason=(
        "Excellent SDK and trivial to install. The documentation does not show "
        "diagram rendering, which is a hard requirement."))}

    assert "impl_true_reason_negative" in codes(metrics)


def test_praise_for_needing_no_credentials_is_not_a_missing_requirement():
    """"does not require" is a compliment; flagging it would train readers to ignore the section."""
    metrics = {"alpha": row(reason=(
        "The public endpoint does not require an API key and does not need an "
        "account, so integration is trivial."))}

    assert codes(metrics) == []


def test_a_failing_row_claiming_it_meets_the_requirements_is_flagged():
    """The third escape: "meets all hard requirements" printed beside implementable false."""
    metrics = {"alpha": row(rating=40, implementable=False, reason=(
        "The API meets all the hard requirements stated in the objective."))}

    assert "impl_false_reason_positive" in codes(metrics)


def test_a_hedged_partial_claim_is_not_an_assertion_of_satisfaction():
    """"supports most of" is the honest half-answer the check must leave alone."""
    metrics = {"alpha": row(rating=40, implementable=False, reason=(
        "It supports most of the documented formats but the objective's output "
        "form is absent."))}

    assert codes(metrics) == []


def test_a_component_failed_for_one_specific_capability_is_flagged():
    """Nodemailer failed for "no webhook support" — a capability the framework above it supplies."""
    metrics = {"alpha": row(rating=40, implementable=False, role="build_component", reason=(
        "The library does not support scheduled jobs with retry and backoff, "
        "which is a hard requirement."))}

    assert "component_failed_specific" in codes(metrics)


def test_a_component_failed_for_covering_none_of_the_capabilities_is_not_flagged():
    """The rule's own wording for a legitimate component failure must pass silently."""
    metrics = {"alpha": row(rating=40, implementable=False, role="build_component", reason=(
        "The documentation does not show question generation, diagram rendering, "
        "or delivery; it contributes to none of the objective's required "
        "capabilities."))}

    assert "component_failed_specific" not in codes(metrics)


def test_a_product_failed_for_one_specific_capability_is_not_a_component_flag():
    """A product is judged against the whole objective, so naming the gap is correct there."""
    metrics = {"alpha": row(rating=40, implementable=False, role="product", reason=(
        "The documentation does not show diagram rendering, which is a hard "
        "requirement."))}

    assert codes(metrics) == []


def test_a_failing_row_scored_above_the_failure_cap_is_flagged():
    """result_from_plan caps a failure at 49; anything higher never came out of it."""
    metrics = {"alpha": row(rating=80, implementable=False, reason="Documented and maintained.")}

    assert "score_above_failure_cap" in codes(metrics)


def test_a_failing_row_at_the_cap_is_not_flagged():
    metrics = {"alpha": row(rating=49, implementable=False, reason="Documented and maintained.")}

    assert codes(metrics) == []


def test_a_high_score_with_no_evidence_is_flagged():
    """At 70 a reader stops reading and starts trusting; nothing under it is a claim."""
    metrics = {"alpha": row(rating=91, evidence=[])}

    assert codes(metrics) == ["high_score_no_evidence"]


def test_an_unavailable_row_is_never_flagged():
    """A withheld score is already honest: it has no number to contradict."""
    metrics = {"alpha": tool_assessment.unavailable_result(
        "Assessment unavailable: the documentation does not show anything at all.")}

    assert codes(metrics) == []


def test_a_row_with_no_verdict_is_never_flagged():
    """implementable None is an absent flag, not a claim either way."""
    metrics = {"alpha": row(implementable=None, reason=(
        "The documentation does not show diagram rendering."))}

    assert codes(metrics) == []


# ------------------------------------------------------------------- the repair

def _plan(**overrides) -> dict:
    value = {
        "implementable": False, "execution_mode": "comparison_only",
        "reason": "The documentation shows no rendering of any required output form.",
        "documentation_quality": 60, "integration_feasibility": 50,
        "auth_clarity": 40, "setup_complexity": 2,
        "build_commands": [], "verification_code": "", "evidence": ["documented fact"],
    }
    value.update(overrides)
    return value


# Two providers so a DISTINCT supervisor resolves: assessment primary is DeepSeek
# (first configured in the assessment order), and the supervisor is Moonshot (the
# first configured provider in the supervision order whose identity differs). The
# re-assessment therefore never runs on the model that produced the assessment.
SUP_ENV = {"MOONSHOT_API_KEY": "sk-supervisor", "DEEPSEEK_API_KEY": "sk-assessment"}


def _serve(monkeypatch, results):
    seen = {}

    def fake(candidates, objective, identity, env, constraints):
        seen["candidates"] = candidates
        seen["objective"] = objective
        seen["identity"] = identity
        return results

    monkeypatch.setattr(self_check, "_supervised_reassessment", fake)
    return seen


SCRAPED = {"alpha": {"name": "alpha", "docs_text": "alpha docs", "role": "product"}}


def test_a_validating_reassessment_replaces_the_row(monkeypatch):
    """A DISTINCT model resolves the contradiction; code only carries it across."""
    seen = _serve(monkeypatch, {"alpha": {"plan": validate_plan(_plan())}})
    metrics = {"alpha": row(rating=91, reason=(
        "The documentation does not show diagram rendering, which is a hard "
        "requirement."))}

    result = repair(metrics, "objective", find_contradictions(metrics), SCRAPED,
                    env=SUP_ENV)

    assert result["repaired"] == ["alpha"]
    assert result["metrics"]["alpha"]["implementable"] is False
    assert result["metrics"]["alpha"]["rating"] <= 49
    # The row is a run's row, not a plan's: what the run stamped survives.
    assert result["metrics"]["alpha"]["display_name"] == "Alpha"
    # The re-assessment ran on a model distinct from the assessment's own.
    assert seen["identity"].provider == "moonshot"
    assert result["supervisor"] == seen["identity"].label()


def test_no_distinct_supervisor_publishes_the_flag_rather_than_self_review(monkeypatch):
    """With one provider, re-asking the SAME model is the correlated review we reject."""
    def unexpected(*_a, **_k):
        raise AssertionError("a lone provider must not re-review its own assessment")

    monkeypatch.setattr(self_check, "_supervised_reassessment", unexpected)
    original = row(rating=91, reason=(
        "The documentation does not show rendering, which is a hard requirement."))
    metrics = {"alpha": dict(original)}

    # Only the assessment provider is configured — no distinct reviewer exists.
    result = repair(metrics, "objective", find_contradictions(metrics), SCRAPED,
                    env={"DEEPSEEK_API_KEY": "sk-assessment"})

    assert result["repaired"] == []
    assert result["supervisor"] is None
    assert result["metrics"]["alpha"] == original


def test_the_reassessment_supervisor_is_never_drawn_from_the_fallback_chain(monkeypatch):
    """A batch does not record which provider produced each row, and assessment
    falls back across its whole configured chain — so any provider in that chain
    may have produced a given row. A reviewer drawn from the chain could be
    re-reviewing its own output, so with only chain providers configured the
    conservative, honest answer is no supervisor and the flag is kept."""
    def unexpected(*_a, **_k):
        raise AssertionError("a chain provider must not re-review its own assessment")

    monkeypatch.setattr(self_check, "_supervised_reassessment", unexpected)
    original = row(rating=91, reason=(
        "The documentation does not show rendering, which is a hard requirement."))
    metrics = {"alpha": dict(original)}

    # Both configured providers are in the assessment chain (doubleword, openrouter);
    # openrouter is the only distinct supervision provider but is itself a possible
    # producer, so no independent reviewer exists.
    result = repair(metrics, "objective", find_contradictions(metrics), SCRAPED,
                    env={"DOUBLEWORD_API_KEY": "1", "OPENROUTER_API_KEY": "1"})

    assert result["repaired"] == []
    assert result["supervisor"] is None
    assert result["metrics"]["alpha"] == original


def test_a_provider_outside_the_fallback_chain_may_reassess(monkeypatch):
    """Independence is still possible: a provider the assessment chain never uses
    can serve as the distinct reviewer."""
    seen = _serve(monkeypatch, {"alpha": {"plan": validate_plan(_plan())}})
    metrics = {"alpha": row(rating=91, reason=(
        "The documentation does not show rendering, which is a hard requirement."))}

    # Moonshot is outside the assessment chain (doubleword, openrouter), so it is
    # the honest distinct reviewer.
    result = repair(metrics, "objective", find_contradictions(metrics), SCRAPED,
                    env={"DOUBLEWORD_API_KEY": "1", "OPENROUTER_API_KEY": "1",
                         "MOONSHOT_API_KEY": "1"})

    assert result["repaired"] == ["alpha"]
    assert seen["identity"].provider == "moonshot"


def test_a_failed_reassessment_keeps_the_original_row(monkeypatch):
    """A row we could not re-derive is still a row the run genuinely measured."""
    _serve(monkeypatch, {"alpha": {"error": "ValueError: invalid response"}})
    original = row(rating=91, reason="The documentation does not show rendering.")
    metrics = {"alpha": dict(original)}

    result = repair(metrics, "objective", find_contradictions(metrics), SCRAPED,
                    env=SUP_ENV)

    assert result["repaired"] == []
    assert result["metrics"]["alpha"] == original


def test_a_provider_outage_during_repair_changes_nothing(monkeypatch):
    def dead(*_a, **_k):
        raise RuntimeError("every configured supervisor provider failed")

    monkeypatch.setattr(self_check, "_supervised_reassessment", dead)
    original = row(rating=91, reason="The documentation does not show rendering.")
    metrics = {"alpha": dict(original)}

    result = repair(metrics, "objective", find_contradictions(metrics), SCRAPED,
                    env=SUP_ENV)

    assert result["repaired"] == []
    assert result["metrics"]["alpha"] == original


def test_only_self_contradictions_are_reassessed(monkeypatch):
    """A corrupted score and a thin evidence list are not arguments a model can settle."""
    seen = _serve(monkeypatch, {"alpha": {"plan": validate_plan(_plan())}})
    metrics = {
        "alpha": row(rating=91, reason=(
            "The documentation does not show rendering, which is a hard requirement.")),
        # score_above_failure_cap: surfaced, never papered over.
        "beta": row(rating=80, implementable=False, reason="Documented and maintained."),
        # high_score_no_evidence: a quality smell, not a contradiction.
        "gamma": row(rating=91, evidence=[]),
    }
    scraped = {name: {"name": name, "docs_text": f"{name} docs", "role": "product"}
               for name in metrics}

    repair(metrics, "objective", find_contradictions(metrics), scraped, env=SUP_ENV)

    assert [item["name"] for item in seen["candidates"]] == ["alpha"]


def test_the_repair_request_quotes_the_contradiction_back(monkeypatch):
    """A blind retry of a deterministic request repeats the mistake."""
    seen = _serve(monkeypatch, {"alpha": {"plan": validate_plan(_plan())}})
    metrics = {"alpha": row(rating=91, reason=(
        "The documentation does not show rendering, which is a hard requirement."))}

    repair(metrics, "objective", find_contradictions(metrics), SCRAPED, env=SUP_ENV)

    note = seen["candidates"][0]["note"]
    assert "contradicted itself" in note
    assert "does not show" in note
    assert "implementable must be false" in note


def test_the_note_reaches_the_assessment_prompt():
    """The repair reuses the retry's shape: appended to the same user message."""
    request = tool_assessment._assessment_request(
        "alpha", "docs", "objective", [], note="\n\nIMPORTANT: contradicted itself.")

    assert request["messages"][-1]["content"].endswith("IMPORTANT: contradicted itself.")


def test_a_surviving_contradiction_is_reported_not_hidden(monkeypatch):
    """The model kept its story; the reader gets the argument rather than the number alone."""
    _serve(monkeypatch, {"alpha": {"plan": validate_plan(_plan(
        implementable=True,
        reason="The documentation does not show rendering, which is a hard requirement."))}})
    metrics = {"alpha": row(rating=91, reason=(
        "The documentation does not show rendering, which is a hard requirement."))}

    outcome = run_self_check(metrics, "objective", SCRAPED, env=SUP_ENV)

    assert outcome["repaired"] == ["alpha"]
    assert [flag["code"] for flag in outcome["flags"]] == ["impl_true_reason_negative"]


def test_a_clean_set_of_rows_never_calls_the_reassessment(monkeypatch):
    """The judge may read a clean row, but only a contradiction buys a re-assessment."""
    def unexpected(*_a, **_k):
        raise AssertionError("a clean run must not spend a second assessment")

    monkeypatch.setattr(self_check, "_supervised_reassessment", unexpected)
    monkeypatch.setattr(self_check, "_judged_contradictions", lambda *_a, **_k: [])

    assert run_self_check({"alpha": row()}, "objective", SCRAPED, env=SUP_ENV) == {
        "flags": [], "repaired": []}


# ------------------------------------------------------------------- the judge

JUDGED_FLAG = {"name": "alpha", "code": "verdict_reason_judged_contradictory",
               "detail": ("Rated implementable, but a distinct reviewer judged the "
                          "reason to argue the opposite: “Rendering is nowhere in "
                          "the documentation.”")}


def _judge(monkeypatch, flags):
    seen = {}

    def fake(rows, objective, identity, env):
        seen["rows"] = rows
        seen["identity"] = identity
        return flags

    monkeypatch.setattr(self_check, "_judged_contradictions", fake)
    return seen


def test_a_rephrased_contradiction_the_regexes_missed_is_judged_and_repaired(monkeypatch):
    """"is nowhere in the documentation" asserts the same absence as "does not
    show" and matches no regex; only the judge catches the rephrasing."""
    _judge(monkeypatch, [dict(JUDGED_FLAG)])
    _serve(monkeypatch, {"alpha": {"plan": validate_plan(_plan())}})
    metrics = {"alpha": row(rating=91, reason=(
        "Rendering of the required diagram output is nowhere in the documentation."))}

    assert find_contradictions(metrics) == []
    outcome = run_self_check(metrics, "objective", SCRAPED, env=SUP_ENV)

    assert outcome["repaired"] == ["alpha"]
    assert metrics["alpha"]["implementable"] is False
    # The replacement came from the distinct supervisor with the contradiction
    # quoted back; re-judging it would be a retry loop, so the flag retires.
    assert outcome["flags"] == []


def test_the_judge_only_sees_rows_the_regexes_cleared(monkeypatch):
    """A row the cheap pass already caught is repaired, not judged twice; a row
    with no verdict or no reason has no pair of claims to contradict."""
    seen = _judge(monkeypatch, [])
    _serve(monkeypatch, {})
    metrics = {
        "alpha": row(rating=91, reason=(
            "The documentation does not show rendering, which is a hard requirement.")),
        "beta": row(reason="Documented client and clear auth."),
        "gamma": row(rating=None),
        "delta": row(implementable=None),
        "epsilon": row(reason=""),
    }

    run_self_check(metrics, "objective", SCRAPED, env=SUP_ENV)

    assert [item["name"] for item in seen["rows"]] == ["beta"]
    assert seen["identity"].provider == "moonshot"


def test_no_distinct_supervisor_skips_the_judge_entirely(monkeypatch):
    """The judge holds the repair's line: no distinct model means no review,
    never the producer reading its own prose back."""
    def unexpected(*_a, **_k):
        raise AssertionError("a lone provider must not judge its own rows")

    monkeypatch.setattr(self_check, "_judged_contradictions", unexpected)

    outcome = run_self_check({"alpha": row()}, "objective", SCRAPED,
                             env={"DEEPSEEK_API_KEY": "sk-assessment"})

    assert outcome == {"flags": [], "repaired": []}


def test_a_judge_outage_leaves_the_cleared_rows_unflagged(monkeypatch):
    """Fail-open: the check must never break the run, and a judge that fails
    only returns the run to the regex-clean state it already earned."""
    def dead(*_a, **_k):
        raise RuntimeError("every configured supervisor provider failed")

    monkeypatch.setattr(self_check, "_judged_contradictions", dead)

    assert run_self_check({"alpha": row()}, "objective", SCRAPED, env=SUP_ENV) == {
        "flags": [], "repaired": []}


def test_a_judged_flag_survives_when_the_reassessment_fails(monkeypatch):
    """An unrepaired row still carries the contradiction the judge found; the
    reader gets the argument rather than the number alone."""
    _judge(monkeypatch, [dict(JUDGED_FLAG)])
    _serve(monkeypatch, {"alpha": {"error": "ValueError: invalid response"}})
    metrics = {"alpha": row(rating=91, reason=(
        "Rendering of the required diagram output is nowhere in the documentation."))}

    outcome = run_self_check(metrics, "objective", SCRAPED, env=SUP_ENV)

    assert outcome["repaired"] == []
    assert [flag["code"] for flag in outcome["flags"]] == [
        "verdict_reason_judged_contradictory"]


def _judge_provider(monkeypatch, batches):
    """Serve one canned reply batch per judge call, recording every request batch."""
    from types import SimpleNamespace

    from engine import llm_clients

    calls = []

    async def fake(provider, requests, model=None, env=None):
        calls.append(requests)
        return [item if isinstance(item, BaseException) else SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))])
            for item in batches[len(calls) - 1]]

    monkeypatch.setattr(llm_clients, "provider_chat_completions", fake)
    return calls


def _judge_rows(*names):
    return [{"name": name, "implementable": True, "reason": "reason text"}
            for name in names]


def _run_judge(rows):
    from engine.llm_clients import ModelIdentity

    return self_check._judged_contradictions(
        rows, "objective", ModelIdentity("moonshot", "kimi"), {})


def test_a_well_formed_verdict_stands_on_the_first_reply(monkeypatch):
    """Contradictory flags, consistent clears — and neither buys a second call:
    a second opinion is one opinion, never a retry hunting for a flag."""
    calls = _judge_provider(monkeypatch, [[
        json.dumps({"verdict": "contradictory",
                    "sentence": "Rendering is nowhere in the documentation."}),
        json.dumps({"verdict": "consistent", "sentence": ""}),
    ]])

    flags = _run_judge(_judge_rows("alpha", "beta"))

    assert [flag["name"] for flag in flags] == ["alpha"]
    assert "Rendering is nowhere in the documentation." in flags[0]["detail"]
    assert len(calls) == 1


def test_a_malformed_contradictory_reply_is_repaired_once_and_the_flag_lands(monkeypatch):
    """The judge decided; only the envelope failed. A noun-form verdict, a
    prose-wrapped JSON object, and a trailing period all vanished silently
    before — a broken-but-willing judge read as a clean bill of health."""
    well_formed = json.dumps({"verdict": "contradictory",
                              "sentence": "Rendering is nowhere shown."})
    calls = _judge_provider(monkeypatch, [
        [json.dumps({"verdict": "contradiction", "sentence": "Rendering is nowhere shown."}),
         "Here is my analysis:\n" + json.dumps(
             {"verdict": "contradictory", "sentence": "Rendering is nowhere shown."}),
         json.dumps({"verdict": "contradictory.", "sentence": "Rendering is nowhere shown."})],
        [well_formed, well_formed, well_formed],
    ])

    flags = _run_judge(_judge_rows("alpha", "beta", "gamma"))

    assert [flag["name"] for flag in flags] == ["alpha", "beta", "gamma"]
    assert len(calls) == 2
    # The retry quotes the failure and the malformed reply back, the same shape
    # as the assessment retry — a blind re-ask would just repeat the mistake.
    retry = calls[1][0]["messages"][-1]["content"]
    assert "failed validation" in retry
    assert "'contradiction'" in retry
    assert "keeping the judgement you already reached" in retry


def test_the_judge_repair_is_bounded_to_a_single_round(monkeypatch):
    """A reply still malformed after the failure was quoted back is dropped:
    one bounded repair, never a loop that asks until something parses."""
    calls = _judge_provider(monkeypatch, [["not json at all"], ["still not json"]])

    assert _run_judge(_judge_rows("alpha")) == []
    assert len(calls) == 2


def test_an_errored_request_gets_no_repair_round(monkeypatch):
    """An exception carries no reply to quote back, so its row is dropped
    without a retry — the judge pass stays fail-open, never a gate."""
    calls = _judge_provider(monkeypatch, [[RuntimeError("request failed")]])

    assert _run_judge(_judge_rows("alpha")) == []
    assert len(calls) == 1


def test_a_repaired_consistent_verdict_is_not_re_asked_for_a_flag(monkeypatch):
    """The repair fixes the envelope, not the verdict: a retry that validates
    to consistent clears its row exactly as a first-attempt consistent does."""
    calls = _judge_provider(monkeypatch, [
        ["The row looks fine to me."],
        [json.dumps({"verdict": "consistent", "sentence": ""})],
    ])

    assert _run_judge(_judge_rows("alpha")) == []
    assert len(calls) == 2


def test_the_judge_request_shows_the_row_and_demands_strict_json():
    """The judge sees exactly what a reader of the row sees — verdict and reason —
    and is pinned to deterministic strict-JSON output like the assessment is."""
    request = self_check._judge_request(
        "alpha", True, "Rendering is nowhere in the documentation.", "the objective")

    assert request["temperature"] == 0
    assert request["response_format"] == {"type": "json_object"}
    content = request["messages"][-1]["content"]
    assert "implementable" in content
    assert "Rendering is nowhere in the documentation." in content
    assert '"contradictory" or "consistent"' in content


# ------------------------------------------------------------------ the report

def test_surviving_flags_are_printed_after_the_findings(tmp_path):
    metrics = {"alpha": row(rating=91, reason="Rated well.")}
    flags = [{"name": "alpha", "code": "impl_true_reason_negative",
              "detail": "Rated implementable, but the reason asserts a missing capability"}]

    markdown = write_assessment_report(
        metrics, [], str(tmp_path / "report.md"), self_check=flags)

    assert "## Self-check" in markdown
    assert markdown.index("## Findings") < markdown.index("## Self-check")
    assert "- **Alpha** — Rated implementable, but the reason asserts a missing capability" in markdown
    assert "re-assessed once; the flags below survived" in markdown


def test_a_clean_run_adds_no_self_check_section(tmp_path):
    """Silence is the healthy state: a heading that always appears teaches readers to skip it."""
    markdown = write_assessment_report(
        {"alpha": row()}, [], str(tmp_path / "report.md"), self_check=[])

    assert "## Self-check" not in markdown


# ------------------------------------------------------- the agent-level wrapper

def _run(monkeypatch, plan_value=None):
    from engine import agent

    events = []
    run_dir = Path("runs") / f"test_self_check_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool",
                        lambda name, args, ctx: json.dumps("official docs"))
    # The judge resolves a supervisor from the runtime env — on a developer
    # machine with real keys that would be a live call. These tests are about
    # the agent wiring, not the judge, so it clears every row it is shown.
    monkeypatch.setattr(self_check, "_judged_contradictions", lambda *_a, **_k: [])
    monkeypatch.setattr(
        tool_assessment, "assess_documentation_batch",
        lambda candidates, *_a, **_k: {
            c["name"]: {"plan": validate_plan(plan_value or _plan())} for c in candidates
        },
    )
    orchestrator = agent.Orchestrator(
        "test-self-check", str(run_dir), lambda event, data: events.append((event, data)))
    try:
        metrics = orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "c", "objective": "o",
            "candidates": [{"name": "example", "display_name": "Example",
                            "docs_url": "https://example.com/docs", "kind": "saas"}],
        })
        return metrics, events
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_a_self_check_that_explodes_does_not_fail_the_run(monkeypatch):
    """The review is a second opinion. It must never erase evidence a run measured."""
    def explode(*_a, **_k):
        raise RuntimeError("consistency review blew up")

    monkeypatch.setattr(self_check, "run_self_check", explode)
    metrics, events = _run(monkeypatch)

    assert metrics["example"]["rating"] is not None
    traces = [data for kind, data in events
              if kind == "artifact" and data.get("tool") == "self_check"]
    assert [trace["status"] for trace in traces] == ["error"]


def test_a_healthy_run_still_emits_one_self_check_trace(monkeypatch):
    metrics, events = _run(monkeypatch)

    traces = [data for kind, data in events
              if kind == "artifact" and data.get("tool") == "self_check"]
    assert len(traces) == 1
    assert traces[0]["status"] == "ok"
    assert traces[0]["detail"] == "repaired 0; 0 flags remain"
