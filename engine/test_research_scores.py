"""Research scoring for candidates a benchmark could not execute.

The invariant under test is the same one the evaluator holds: a number is
either evidence or it is absent. These scores are documentation evidence, they
are named as such, and they never appear where a measurement belongs.
"""

from __future__ import annotations

import pytest

from engine import research_scores as rs


CANDIDATES = [
    {"name": "affinda", "docs_url": "https://example.com/affinda"},
    {"name": "tesseract", "docs_url": "https://example.com/tesseract"},
]


def plan(**overrides):
    value = {
        "implementable": True,
        "reason": "The API documents an invoice endpoint returning the requested fields.",
        "documentation_quality": 80,
        "integration_feasibility": 84,
        "auth_clarity": 70,
        "setup_complexity": 3,
        "build_commands": ["pip install example-sdk"],
        "verification_code": "import example\nprint('PROOFBENCH_OK')",
        "evidence": ["The docs show a POST /invoices endpoint."],
    }
    value.update(overrides)
    return value


def batch(monkeypatch, result):
    """Stand in for the provider batch call, recording what it was asked."""
    seen = {}

    def fake(candidates, objective, **kwargs):
        seen["candidates"] = list(candidates)
        seen["objective"] = objective
        seen["kwargs"] = kwargs
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(rs, "assess_documentation_batch", fake)
    return seen


def test_objective_states_the_fields_the_candidates_must_extract():
    objective = rs.extraction_objective(
        {"category": "invoice_extraction", "fields": ["invoice_number", "total"]}
    )
    assert "invoice extraction" in objective
    assert "invoice_number" in objective and "total" in objective


def test_an_explicit_objective_is_never_rewritten():
    assert rs.extraction_objective({"objective": "Read scanned receipts."}) == (
        "Read scanned receipts."
    )


def test_documentation_produces_a_score_on_its_own_named_basis(monkeypatch):
    from engine.tool_assessment import validate_plan

    batch(monkeypatch, {"affinda": {"plan": validate_plan(plan())}})
    scored = rs.research_scores(CANDIDATES, "objective", scrape=lambda url: "docs text")

    assert scored["affinda"]["research_basis"] == "documentation_evidence"
    assert 0 < scored["affinda"]["research_score"] <= 100
    assert scored["affinda"]["documentation_quality"] == 80
    assert scored["affinda"]["implementable"] is True
    # Nothing ran, so nothing may claim it did.
    assert "exact_accuracy" not in scored["affinda"]


def test_unreadable_documentation_is_scored_as_nothing_at_all(monkeypatch):
    from engine.tool_assessment import validate_plan

    unreadable = validate_plan(plan(
        implementable=False,
        documentation_quality=0,
        integration_feasibility=0,
        auth_clarity=0,
        build_commands=[],
        verification_code="",
    ))
    batch(monkeypatch, {"affinda": {"plan": unreadable}})
    assert rs.research_scores(CANDIDATES, "o", scrape=lambda url: "docs") == {}


def test_a_failed_scrape_removes_that_candidate_from_the_request(monkeypatch):
    from engine.tool_assessment import validate_plan

    seen = batch(monkeypatch, {"tesseract": {"plan": validate_plan(plan())}})

    def scrape(url):
        if "affinda" in url:
            raise RuntimeError("scrape failed")
        return "docs text"

    scored = rs.research_scores(CANDIDATES, "o", scrape=scrape)
    assert [item["name"] for item in seen["candidates"]] == ["tesseract"]
    assert "affinda" not in scored


def test_a_provider_outage_produces_no_scores_rather_than_low_ones(monkeypatch):
    batch(monkeypatch, RuntimeError("no assessment provider is configured"))
    assert rs.research_scores(CANDIDATES, "o", scrape=lambda url: "docs") == {}


def test_a_provider_error_for_one_candidate_leaves_the_others_scored(monkeypatch):
    from engine.tool_assessment import validate_plan

    batch(monkeypatch, {
        "affinda": {"error": "ValueError: invalid response"},
        "tesseract": {"plan": validate_plan(plan())},
    })
    scored = rs.research_scores(CANDIDATES, "o", scrape=lambda url: "docs")
    assert set(scored) == {"tesseract"}


def test_merge_never_overwrites_a_measurement():
    metrics = {
        "tesseract": {"exact_accuracy": 0.667, "field_f1": 0.79, "status": "ok",
                      "setup_complexity": 2},
        "affinda": {"exact_accuracy": None, "field_f1": None, "status": "no_result",
                    "setup_complexity": 1,
                    "error_summary": "adapter validation failed"},
    }
    scored = {
        "tesseract": {"research_score": 70, "documentation_quality": 60,
                      "setup_complexity": 5},
        "affinda": {"research_score": 88, "documentation_quality": 90,
                    "setup_complexity": 3},
    }
    rs.merge_research_scores(metrics, scored, curated_setup={"tesseract"})

    assert metrics["tesseract"]["exact_accuracy"] == 0.667
    assert metrics["tesseract"]["research_score"] == 70
    # Curated setup complexity is real evidence and is kept.
    assert metrics["tesseract"]["setup_complexity"] == 2
    # The unmeasured row is scored, and its withheld measurement stays withheld.
    assert metrics["affinda"]["research_score"] == 88
    assert metrics["affinda"]["exact_accuracy"] is None
    assert metrics["affinda"]["status"] == "no_result"
    assert metrics["affinda"]["error_summary"] == "adapter validation failed"
    # No curated entry, so the assessed value replaces the table's default of 1.
    assert metrics["affinda"]["setup_complexity"] == 3


def test_merge_leaves_an_unscored_row_exactly_as_it_was():
    metrics = {"mindee": {"exact_accuracy": None, "status": "no_result"}}
    rs.merge_research_scores(metrics, {})
    assert metrics == {"mindee": {"exact_accuracy": None, "status": "no_result"}}


def test_merge_does_not_mutate_the_scores_it_was_given():
    scored = {"affinda": {"research_score": 88, "setup_complexity": 3}}
    rs.merge_research_scores({"affinda": {"status": "no_result"}}, scored)
    assert scored["affinda"]["setup_complexity"] == 3


@pytest.mark.parametrize("candidate", [
    {"name": "", "docs_url": "https://example.com"},
    {"name": "affinda", "docs_url": ""},
])
def test_a_candidate_without_a_name_or_documentation_is_never_requested(monkeypatch, candidate):
    seen = batch(monkeypatch, {})
    rs.research_scores([candidate], "o", scrape=lambda url: "docs")
    assert seen == {} or seen["candidates"] == []
