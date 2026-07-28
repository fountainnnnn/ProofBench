"""One query dialect draws from one pool, forever.

Measured: repeated runs of the same question came back with the same handful of
popular tools, while a known-good niche product never surfaced at all — and no
human could find it either, searching in the words the user used. The product is
not hidden; it ranks fine in its own dialect. A capability a buyer calls question
generation is a worksheet generator to a teacher, a homework app in a store
listing, and a question bank API to a developer, and each of those phrasings
returns a different pool. Searching one of them is searching one pool.

These tests pin the sweep that fixes it: the dialect rule, its search budget, and
the honest record of what the sweep surfaced but the shortlist left behind.
"""
from __future__ import annotations

import pytest

from engine import agent as agent_mod
from engine.agent import _normalize_intake_spec
from engine.tool_assessment import write_assessment_report


def flat(dataset_available: bool = False) -> str:
    """The prompt with its line wrapping removed, as the other intake tests do."""
    return " ".join(agent_mod.intake_system(dataset_available).split())


# ------------------------------------------------------------------ the sweep

def test_intake_is_told_to_search_in_more_than_one_dialect():
    prompt = flat()

    assert "DIFFERENT NAMES this capability goes by" in prompt
    assert "3 to 5" in prompt
    # The four audiences, because dropping one drops a pool.
    for audience in ("buyer", "practitioner", "consumer", "developer"):
        assert audience in prompt
    assert "app-store language" in prompt
    assert "library/API language" in prompt
    # Each query is phrased in the audience's words, not the user's.
    assert "phrasing each query in that audience's own words, not the user's" in prompt
    assert "ONE dialect among several, never the only one" in prompt


def test_the_two_extra_query_shapes_are_offered():
    prompt = flat()

    assert "what do <practitioners> use to <job>" in prompt
    assert "<dominant well-known tool> alternatives" in prompt


def test_the_sweep_climbs_one_rung_up_the_category_ladder():
    """Every dialect used the user's subject term, so a product that markets itself at
    the broader category was invisible in all of them."""
    prompt = flat()

    assert "SUPERORDINATE CATEGORY" in prompt
    assert "one rung up the category ladder" in prompt
    # The concept is taught with a generic subject-vs-category pair, never a brand.
    assert 'subject-level "math practice" is to category-level "STEM learning"' in prompt


def test_the_sweep_shapes_one_query_for_app_store_distribution():
    """A store-first product ranks as a store listing; tool-shaped queries never reach it."""
    prompt = flat()

    assert "CHANNEL-SHAPED for app-store distribution" in prompt
    assert '"<job> app iphone"' in prompt
    assert '"<job> app android"' in prompt
    assert "individual end user" in prompt
    assert "store listings rather" in prompt


def test_the_two_new_shapes_live_in_the_dialect_rule_before_the_budget():
    """Both are dialect-sweep rules; the budget that counts them comes after."""
    prompt = flat()

    ladder = prompt.index("SUPERORDINATE CATEGORY")
    channel = prompt.index("CHANNEL-SHAPED for app-store distribution")
    budget = prompt.index("at most 6 web_search calls")
    assert prompt.index("DIFFERENT NAMES this capability goes by") < ladder < budget
    assert ladder < channel < budget


def test_the_budget_says_what_the_sweep_spends_it_on_first():
    """Six searches, and the two new shapes come out of the same six."""
    prompt = flat()

    assert "count inside that budget" in prompt
    assert "the sweep PRIORITISES: the user's phrasing, one other audience dialect, " \
           "the category-ladder query, and the channel query when an individual end " \
           "user is plausible" in prompt
    assert "whatever budget remains goes to more dialects" in prompt


def test_the_dialect_rule_comes_before_the_search_budget_it_governs():
    """A budget of six searches only makes sense once the sweep is defined."""
    prompt = flat()

    assert prompt.index("DIFFERENT NAMES this capability goes by") < prompt.index(
        "at most 6 web_search calls"
    )


def test_the_sweep_has_a_budget_and_repetition_is_what_is_forbidden():
    """The old cap was one round per candidate, which never named the real fault.

    Six searches are cheap; six near-identical searches are the bug — they redraw
    from the pool the first one already emptied.
    """
    prompt = flat()

    assert "at most 6 web_search calls" in prompt
    assert "DIFFERENT dialect or query shape" in prompt
    assert "Repeating a near-identical query is the one thing that is forbidden" in prompt
    # And the run still has to end with a spec: this is why the budget exists.
    assert "leaving the user without one is the worst outcome" in prompt


def test_the_budget_fits_inside_the_intake_loop():
    """Six searches plus the review passes must not need a bigger loop."""
    assert agent_mod.INTAKE_ROUNDS == 14


def test_the_shortlist_spans_the_dialects_the_sweep_reached():
    prompt = flat()

    assert "Shortlist 4-8 candidates" in prompt
    assert "chosen across dialects" in prompt
    assert "a shortlist drawn from a single dialect wastes the sweep" in prompt
    # Build components are required separately and never eat a product slot.
    assert "Only products count toward 4-8" in prompt


def test_the_extraction_variant_keeps_the_sweep():
    """It is built from the base prompt and must not lose the rule."""
    prompt = flat(dataset_available=True)

    assert "DIFFERENT NAMES this capability goes by" in prompt
    assert "at most 6 web_search calls" in prompt


def test_the_sweep_names_no_products():
    """Dialect examples are categories. A brand in the prompt is a thumb on the scale."""
    prompt = flat(dataset_available=True)

    for vendor in ("Quizlet", "Kahoot", "Canva", "Google", "Microsoft", "OpenAI"):
        assert vendor not in prompt, f"intake prompt names {vendor}"


# ------------------------------------------------- the record of what was cut

def _spec(**extra):
    spec = {
        "benchmark_type": "tool_assessment",
        "category": "Question generation",
        "objective": "Generate practice questions from source material",
        "candidates": [{"name": "alpha", "display_name": "Alpha",
                        "docs_url": "https://example.com/docs", "kind": "saas"}],
    }
    spec.update(extra)
    return spec


def test_a_not_assessed_candidate_survives_normalization():
    """What the sweep surfaced and the shortlist passed over is still on the record."""
    normalized = _normalize_intake_spec(
        _spec(excluded=[{"name": "beta", "display_name": "Beta",
                         "kind": "not_assessed"}]),
        dataset_available=False,
    )

    assert normalized["excluded"] == [{
        "name": "beta",
        "display_name": "Beta",
        "kind": "not_assessed",
        "violates": agent_mod.NOT_ASSESSED_NOTE,
    }]
    assert "no requirement strike recorded" in agent_mod.NOT_ASSESSED_NOTE


def test_a_violation_still_needs_a_stated_constraint():
    """Only the not_assessed line is written for the model; a strike is not."""
    normalized = _normalize_intake_spec(
        _spec(excluded=[
            {"name": "beta", "violates": "Hosted only; the stated constraint is on-prem."},
            {"name": "gamma", "violates": "   "},
        ]),
        dataset_available=False,
    )

    assert [item["name"] for item in normalized["excluded"]] == ["beta"]
    assert normalized["excluded"][0]["kind"] == "violation"


def test_an_unrecognised_exclusion_kind_is_a_violation():
    """The kind that claims less is the safe default, but it is never guessed into."""
    for junk in ("dismissed", "", None, 7, ["not_assessed"]):
        normalized = _normalize_intake_spec(
            _spec(excluded=[{"name": "beta", "kind": junk, "violates": "Hosted only."}]),
            dataset_available=False,
        )
        assert normalized["excluded"][0]["kind"] == "violation"


def test_an_exclusion_cannot_shadow_a_shortlisted_candidate():
    normalized = _normalize_intake_spec(
        _spec(excluded=[{"name": "alpha", "kind": "not_assessed"}]),
        dataset_available=False,
    )

    assert "excluded" not in normalized


def test_an_empty_or_malformed_excluded_list_adds_nothing():
    for junk in (None, [], "beta", 7, [None, 3]):
        normalized = _normalize_intake_spec(_spec(excluded=junk), dataset_available=False)
        assert "excluded" not in normalized


def test_the_review_appends_its_drops_to_what_discovery_recorded(monkeypatch, tmp_path):
    """The two kinds are halves of one record; neither may overwrite the other."""
    import json
    import types

    def _complete(env=None, **kwargs):
        content = json.dumps({"drop": [
            {"name": "hosted", "violates": "Hosted only; the constraint is on-prem."}]})
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])

    monkeypatch.setattr(agent_mod, "_orchestrator_complete", _complete)
    a = agent_mod.Orchestrator(run_id="run-d", run_dir=str(tmp_path / "run"),
                               emit=lambda kind, payload: None)
    a._delta = lambda text: None
    a._state = lambda s: None
    a._check_cancelled = lambda: None

    spec = _spec(
        constraints={"deployment": "on-prem only"},
        candidates=[
            {"name": "hosted", "display_name": "Hosted", "docs_url": "https://e/1",
             "kind": "saas"},
            {"name": "onprem", "display_name": "Onprem", "docs_url": "https://e/2",
             "kind": "local_tool"},
        ],
        excluded=[{"name": "passed_over", "display_name": "Passed Over",
                   "kind": "not_assessed", "violates": agent_mod.NOT_ASSESSED_NOTE}],
    )
    reviewed = a._review_shortlist(spec)

    assert [item["name"] for item in reviewed["excluded"]] == ["passed_over", "hosted"]


# ------------------------------------------------------------- how it reads

def _metrics():
    return {"alpha": {"display_name": "Alpha", "overall": 7.0, "verdict": "recommended"}}


def test_the_two_kinds_share_one_heading_but_not_one_shape(tmp_path):
    out = str(tmp_path / "report.md")
    write_assessment_report(
        _metrics(), [], out,
        excluded=[
            {"name": "hosted", "display_name": "Hosted Cloud", "kind": "violation",
             "violates": "Hosted only; the stated constraint is on-prem."},
            {"name": "passed_over", "display_name": "Passed Over",
             "kind": "not_assessed", "violates": agent_mod.NOT_ASSESSED_NOTE},
        ],
    )
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")

    assert markdown.count("## Considered and excluded") == 1
    # A violation reads as it always has: bolded, with the constraint it broke.
    assert "- **Hosted Cloud**: Hosted only; the stated constraint is on-prem." in markdown
    # A pass-over is one compact line, and claims nothing about the tool.
    assert "- Passed Over — not assessed" in markdown
    assert "**Passed Over**" not in markdown
    assert "No requirement strike is recorded" in markdown
    # Neither is scored.
    assert "Passed Over" not in markdown.split("## Considered and excluded")[0]


def test_a_report_with_only_pass_overs_does_not_claim_a_constraint_ruled_them_out(tmp_path):
    out = str(tmp_path / "report.md")
    write_assessment_report(
        _metrics(), [], out,
        excluded=[{"name": "passed_over", "display_name": "Passed Over",
                   "kind": "not_assessed", "violates": agent_mod.NOT_ASSESSED_NOTE}],
    )
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")

    assert "## Considered and excluded" in markdown
    assert "constraint ruled them out" not in markdown


def test_an_exclusion_without_a_kind_reads_as_a_violation(tmp_path):
    """Specs written before the second kind existed still render unchanged."""
    out = str(tmp_path / "report.md")
    write_assessment_report(
        _metrics(), [], out,
        excluded=[{"name": "hosted", "display_name": "Hosted Cloud",
                   "violates": "Hosted only; the stated constraint is on-prem."}],
    )
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")

    assert "constraint ruled them out" in markdown
    assert "- **Hosted Cloud**: Hosted only; the stated constraint is on-prem." in markdown


# ------------------------------------------------------------- the run schema

def test_the_run_schema_accepts_both_kinds_and_defaults_to_violation():
    from server.schemas import ExcludedCandidateSpec

    default = ExcludedCandidateSpec(name="hosted", violates="Hosted only.")
    assert default.kind == "violation"

    passed_over = ExcludedCandidateSpec(
        name="passed_over", kind="not_assessed", violates=agent_mod.NOT_ASSESSED_NOTE)
    assert passed_over.kind == "not_assessed"

    with pytest.raises(Exception):
        ExcludedCandidateSpec(name="hosted", kind="dismissed", violates="Hosted only.")
