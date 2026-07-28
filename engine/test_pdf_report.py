"""The PDF is the artefact that leaves the tool, so it has to carry the argument.

It used to carry a table and the first four paragraphs of prose: three of four
candidates went unmentioned, the evidence bullets were flattened into one
run-on sentence, no source was cited, and the document never said which tool
had won. These tests pin what must survive the render, and — just as
importantly — what must never be invented during it.
"""
from __future__ import annotations

import pytest

from engine import pdf_report
from engine.pdf_report import _blocks, _inline, _verdict, write_pdf_report

pytest.importorskip("reportlab")

METRICS = {
    "azure_ai_search_openai": {
        "rating": 92, "implementable": True, "assessment_basis": "documentation_evidence",
        "documentation_quality": 90, "integration_feasibility": 95, "auth_clarity": 85,
        "setup_complexity": 3, "reason": "Documentation covers RAG over SharePoint.",
    },
    "langchain_sharepoint": {
        "rating": 61, "implementable": True, "assessment_basis": "documentation_evidence",
        "documentation_quality": 60, "integration_feasibility": 70, "auth_clarity": 40,
        "setup_complexity": 3, "reason": "No complete authentication example.",
    },
    "ragie": {
        "rating": 12, "implementable": False, "assessment_basis": "documentation_evidence",
        "documentation_quality": 20, "integration_feasibility": 10, "auth_clarity": 5,
        "setup_complexity": 5, "reason": "SharePoint connector is Coming Soon.",
    },
}

MARKDOWN = """# ProofBench Tool Implementation Report

Suitability is scored from documentation evidence.

## Ranked assessment

| Rank | Tool | Suitability |
|---:|---|---:|
| 1 | azure_ai_search_openai | 92/100 |

## Findings

### azure_ai_search_openai

Documentation covers RAG with Azure AI Search.

- Supports indexing PDF, Word, text via skills pipeline
- Security inherits SharePoint permissions

### ragie

The SharePoint connector is marked as 'Coming Soon'.

- Credential variable list is empty

## Sources

- [Azure AI Search documentation](https://learn.microsoft.com/azure/search/)
- [Ragie documentation](https://www.ragie.ai/connectors/sharepoint)
"""


def _text(path) -> str:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@pytest.fixture()
def rendered(tmp_path):
    out = tmp_path / "report.pdf"
    write_pdf_report(METRICS, MARKDOWN, str(out))
    return _text(out)


# ------------------------------------------------------------------ the content

def test_every_candidate_reaches_the_pdf(rendered):
    """Sampling the first four paragraphs dropped three candidates out of four."""
    for name in METRICS:
        assert name in rendered, f"{name} is missing from the report"


def test_evidence_bullets_survive_as_evidence(rendered):
    assert "Supports indexing PDF, Word, text via skills pipeline" in rendered
    assert "Security inherits SharePoint permissions" in rendered
    assert "Credential variable list is empty" in rendered


def test_sources_are_cited_with_their_addresses(rendered):
    """A hyperlink alone is unverifiable the moment the report is printed."""
    assert "Azure AI Search documentation" in rendered
    assert "https://learn.microsoft.com/azure/search/" in rendered
    assert "https://www.ragie.ai/connectors/sharepoint" in rendered


def test_the_winner_is_stated_not_merely_rankable(rendered):
    assert "RECOMMENDATION" in rendered
    assert "azure_ai_search_openai" in rendered
    # The margin, in words, so the reader need not subtract two table cells.
    assert "31 points ahead of langchain_sharepoint" in rendered


def test_the_basis_is_stated_so_nothing_reads_as_executed(rendered):
    assert "Documentation" in rendered
    # Matched on a fragment: the note is a wrapped caption, so the full sentence
    # is split across lines in the extracted text.
    assert "never executed" in rendered


def test_the_ranked_table_is_present_with_its_scores(rendered):
    assert "Suitability" in rendered
    assert "92/100" in rendered and "61/100" in rendered


def test_the_markdown_table_is_not_printed_twice(rendered):
    """The table is typeset from metrics; the markdown copy of it is dropped."""
    assert "|---" not in rendered
    assert rendered.count("Ranked comparison") == 1


# -------------------------------------------------------------- what it refuses

def test_a_withheld_score_is_a_dash_and_never_a_zero(tmp_path):
    # 47, not 40: "40/100" contains "0/100", which would let this pass on a
    # report that really had fabricated a zero.
    metrics = {"alpha": {"rating": 47}, "beta": {"rating": None}}
    out = tmp_path / "r.pdf"
    write_pdf_report(metrics, "# R\n\n## Findings\n\nbeta produced nothing.\n", str(out))
    text = _text(out)

    assert "beta" in text
    assert "0/100" not in text


def test_the_pricing_axis_reaches_the_pdf_and_is_dashed_when_withheld(tmp_path):
    """Parity with the markdown table: a docs set that published no prices shows
    a dash, because no pricing score was ever measured for it."""
    metrics = {
        "alpha": {"rating": 77, "implementable": True, "display_name": "alpha",
                  "assessment_basis": "documentation_evidence",
                  "documentation_quality": 80, "integration_feasibility": 84,
                  "auth_clarity": 70, "pricing_transparency": 63, "setup_complexity": 2,
                  "reason": "Published tiers."},
        "beta": {"rating": 55, "implementable": True, "display_name": "beta",
                 "assessment_basis": "documentation_evidence",
                 "documentation_quality": 60, "integration_feasibility": 55,
                 "auth_clarity": 50, "pricing_transparency": None, "setup_complexity": 3,
                 "reason": "Contact sales."},
    }
    out = tmp_path / "r.pdf"
    write_pdf_report(metrics, "# R\n\n## Findings\n\nBoth were assessed.\n", str(out))
    text = _text(out)

    assert "Pricing" in text
    assert "63" in text
    # Beta's withheld pricing is never typeset as a zero.
    assert "\n0\n" not in text


def test_excluded_candidates_survive_the_render(tmp_path):
    """The section is markdown, so it rides the block renderer rather than metrics."""
    out = tmp_path / "r.pdf"
    write_pdf_report(
        {"alpha": {"rating": 70}},
        "# R\n\n## Findings\n\nalpha was assessed.\n\n## Considered and excluded\n\n"
        "These candidates were dropped before assessment. They were not scored.\n\n"
        "- **Gamma Cloud**: Hosted only; the stated constraint is on-prem.\n",
        str(out),
    )
    text = _text(out)

    assert "Considered and excluded" in text
    assert "Gamma Cloud" in text
    assert "not scored" in text


def test_a_run_with_no_result_gets_no_recommendation(tmp_path):
    """Inventing a winner from nothing is the failure this file guards against."""
    out = tmp_path / "r.pdf"
    write_pdf_report({"alpha": {"rating": None}}, "# R\n\n## Findings\n\nNothing ran.\n", str(out))

    assert "RECOMMENDATION" not in _text(out)


def test_a_single_scored_candidate_does_not_claim_to_have_beaten_anything(tmp_path):
    out = tmp_path / "r.pdf"
    write_pdf_report({"alpha": {"rating": 55}, "beta": {"rating": None}},
                     "# R\n\n## Findings\n\nOnly alpha ran.\n", str(out))
    text = _text(out)

    assert "ahead of" not in text
    assert "only candidate" in text.lower()


def test_measured_extraction_results_render_their_own_columns(tmp_path):
    metrics = {
        "tesseract": {"exact_accuracy": 0.93, "field_f1": 0.95, "cer": 0.04,
                      "mean_latency_s": 1.2, "failure_rate": 0.0,
                      "cost_per_1k_docs": 0.0, "setup_complexity": 2},
        "easyocr": {"exact_accuracy": 0.81, "field_f1": 0.86, "cer": 0.09,
                    "mean_latency_s": 2.4, "failure_rate": 0.02,
                    "cost_per_1k_docs": 0.0, "setup_complexity": 3},
    }
    out = tmp_path / "r.pdf"
    write_pdf_report(metrics, "# R\n\n## Findings\n\nBoth ran.\n", str(out))
    text = _text(out)

    assert "MEASURED RESULTS" in text
    assert "93.0%" in text
    assert "12.0 percentage points ahead of easyocr" in text


# ------------------------------------------------------------------- the pieces

def test_report_markup_cannot_close_a_tag_the_renderer_opened():
    """Candidate names and scraped headings are untrusted text."""
    out = _inline("<b>not bold</b> & <script>")
    assert "&lt;b&gt;not bold&lt;/b&gt;" in out
    assert "<script>" not in out


def test_inline_markdown_becomes_markup():
    assert "<b>winner</b>" in _inline("**winner**")
    assert '<link href="https://example.com/"' in _inline("[docs](https://example.com/)")


def test_blocks_separate_headings_bullets_and_prose():
    parsed = _blocks("## Findings\n\nSome prose.\n\n- one\n- two\n\n| a | b |\n|---|---|\n")
    assert ("heading", (2, "Findings")) in parsed
    assert ("para", "Some prose.") in parsed
    assert ("bullet", "one") in parsed
    # Table rows never become prose; the table is rendered from metrics instead.
    assert not any(kind == "para" and "|" in value for kind, value in parsed)


def test_verdict_reports_the_gap_between_the_top_two():
    ranked = [("a", {"rating": 92}), ("b", {"rating": 61})]
    headline, detail = _verdict(ranked, generic=True)
    assert headline == "a — 92/100"
    assert "31 points ahead of b" in detail


def test_a_field_that_all_failed_gets_no_winner():
    """A ranking is not a recommendation.

    The real failure: four candidates all missed a stated key requirement, and
    the report still crowned the least-bad one — "ranked first, 28 points ahead"
    — off a 49/100 whose own reason said the requirement was unmet.
    """
    ranked = [
        ("ragie", {"rating": 49, "implementable": False,
                   "reason": "The SharePoint connector is marked Coming Soon, "
                             "so the stated key requirement is unmet."}),
        ("langchain_sharepoint", {"rating": 21, "implementable": False,
                                  "reason": "No complete authentication example."}),
    ]
    headline, detail = _verdict(ranked, generic=True)

    assert "No candidate met the requirements" in headline
    assert "49/100" in headline
    # It says how many failed, and that the order below is only an ordering.
    assert "not" in detail and "implementable" in detail
    assert "relative" in detail
    assert "2 of 2" in detail
    # The winner's name must never appear as an endorsement headline.
    assert not headline.startswith("ragie")


def test_a_winning_candidate_still_gets_the_ordinary_verdict():
    """The implementable path is untouched: a real winner is still named."""
    ranked = [
        ("azure_ai_search_openai", {"rating": 92, "implementable": True}),
        ("langchain_sharepoint", {"rating": 61, "implementable": False}),
    ]
    headline, detail = _verdict(ranked, generic=True)

    assert headline == "azure_ai_search_openai — 92/100"
    assert "31 points ahead of langchain_sharepoint" in detail
    assert "No candidate met the requirements" not in headline


def test_the_no_winner_verdict_reaches_the_rendered_pdf(tmp_path):
    metrics = {
        "ragie": {"rating": 49, "implementable": False, "display_name": "Ragie",
                  "assessment_basis": "documentation_evidence",
                  "documentation_quality": 55, "integration_feasibility": 45,
                  "auth_clarity": 50, "pricing_transparency": None, "setup_complexity": 5,
                  "reason": "The connector is marked Coming Soon."},
        "langchain_sharepoint": {"rating": 21, "implementable": False,
                                 "display_name": "LangChain", "setup_complexity": 4,
                                 "assessment_basis": "documentation_evidence",
                                 "documentation_quality": 30, "integration_feasibility": 15,
                                 "auth_clarity": 20, "pricing_transparency": None,
                                 "reason": "No complete authentication example."},
    }
    out = tmp_path / "r.pdf"
    write_pdf_report(metrics, "# R\n\n## Findings\n\nBoth were assessed.\n", str(out))
    text = _text(out)

    assert "No candidate met the requirements" in text
    # The candidates are still ranked and printed; only the verdict changed.
    assert "Ragie" in text and "LangChain" in text
    assert "ahead of" not in text


def test_a_build_path_is_recommended_only_when_the_products_all_failed():
    """Earned by the scores. Every marketed product was rated not implementable
    and an assessed component was, so the evidence supports building."""
    ranked = [
        ("object_store_sdk", {"rating": 84, "implementable": True,
                              "role": "build_component"}),
        ("vendor_suite", {"rating": 49, "implementable": False, "role": "product"}),
        ("rival_suite", {"rating": 33, "implementable": False, "role": "product"}),
    ]
    headline, detail = _verdict(ranked, generic=True)

    assert headline == ("No marketed product met the requirements — "
                        "self-implementation is better supported")
    assert "All 2 marketed products" in detail
    assert "object_store_sdk (84/100)" in detail
    # The claim is bounded to what was actually measured.
    assert "documentation evidence, not execution" in detail
    # A component topping the table never produces the old winner headline.
    assert "ahead of" not in detail


def test_components_that_also_failed_are_named_as_not_part_of_the_path():
    ranked = [
        ("sdk_a", {"rating": 80, "implementable": True, "role": "build_component"}),
        ("sdk_b", {"rating": 30, "implementable": False, "role": "build_component"}),
        ("vendor_suite", {"rating": 49, "implementable": False, "role": "product"}),
    ]
    _headline, detail = _verdict(ranked, generic=True)

    assert "sdk_a (80/100)" in detail
    assert "sdk_b" not in detail
    assert "1 component did not clear the bar" in detail


def test_a_failed_field_with_no_viable_component_still_gets_no_winner():
    """The build path is not a consolation prize: components must clear the bar."""
    ranked = [
        ("vendor_suite", {"rating": 49, "implementable": False, "role": "product"}),
        ("sdk_b", {"rating": 30, "implementable": False, "role": "build_component"}),
    ]
    headline, detail = _verdict(ranked, generic=True)

    assert "No candidate met the requirements" in headline
    assert "self-implementation" not in headline
    assert "relative" in detail


def test_a_winning_product_keeps_its_verdict_even_beside_components():
    """Nothing about the build path touches a field that produced a real winner."""
    ranked = [
        ("azure_ai_search_openai", {"rating": 92, "implementable": True, "role": "product"}),
        ("object_store_sdk", {"rating": 84, "implementable": True,
                              "role": "build_component"}),
    ]
    headline, detail = _verdict(ranked, generic=True)

    assert headline == "azure_ai_search_openai — 92/100"
    assert "8 points ahead of object_store_sdk" in detail
    assert "self-implementation" not in headline


def test_verdict_skips_candidates_that_were_never_scored():
    ranked = [("a", {"rating": 40}), ("b", {"rating": None})]
    _headline, detail = _verdict(ranked, generic=True)
    assert "1 scored" not in detail
    assert "only candidate" in detail.lower()


def test_a_failed_render_raises_rather_than_writing_half_a_report(tmp_path, monkeypatch):
    """The orchestrator treats a raise as 'report unavailable'; it must get one."""
    monkeypatch.setattr(pdf_report, "_verdict", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        write_pdf_report(METRICS, MARKDOWN, str(tmp_path / "r.pdf"))
