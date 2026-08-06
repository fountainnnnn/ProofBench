"""Reports should name a tool the way its vendor does.

Metrics are keyed by slug, so every artefact downstream printed
`azure_ai_search_openai` where the spec said "Azure AI Search + Azure OpenAI".
The readable name is stamped onto each metrics row once, at the point the run
finishes, so the console, the markdown and the PDF all get it without each
having to reach back into the spec.
"""
from __future__ import annotations

import pytest

from engine.agent import _spec_display_name
from engine.pdf_report import write_pdf_report
from engine.tool_assessment import write_assessment_report

SPEC = {
    "candidates": [
        {"name": "azure_ai_search_openai", "display_name": "Azure AI Search + Azure OpenAI"},
        {"name": "customgpt", "display_name": "CustomGPT.ai"},
        {"name": "bare"},
    ]
}

METRICS = {
    "azure_ai_search_openai": {
        "rating": 92, "display_name": "Azure AI Search + Azure OpenAI",
        "implementable": True, "assessment_basis": "documentation_evidence",
        "execution_mode": "comparison_only", "reason": "Documented end to end.",
        "evidence": ["Indexes SharePoint"], "verification_status": "not_applicable",
    },
    "customgpt": {
        "rating": 17, "display_name": "CustomGPT.ai",
        "implementable": False, "assessment_basis": "documentation_evidence",
        "execution_mode": "comparison_only", "reason": "Marketing pages only.",
        "evidence": [], "verification_status": "not_implementable",
    },
}


def test_the_spec_supplies_the_readable_name():
    assert _spec_display_name(SPEC, "customgpt") == "CustomGPT.ai"


def test_a_candidate_without_one_keeps_its_slug():
    assert _spec_display_name(SPEC, "bare") == "bare"


def test_an_unknown_or_missing_spec_never_raises():
    """A run whose spec is gone still has to produce a readable report."""
    assert _spec_display_name(SPEC, "not_in_spec") == "not_in_spec"
    assert _spec_display_name(None, "alpha") == "alpha"
    assert _spec_display_name("not a spec", "alpha") == "alpha"


def test_the_markdown_report_uses_display_names(tmp_path):
    markdown = write_assessment_report(METRICS, [], str(tmp_path / "r.md"))

    assert "| Azure AI Search + Azure OpenAI |" in markdown
    assert "### CustomGPT.ai" in markdown
    # The slug is gone from the prose a person reads.
    assert "azure_ai_search_openai" not in markdown


def test_the_markdown_report_escapes_pipes_inside_display_names(tmp_path):
    metrics = {
        "mindgrasp": {
            **METRICS["customgpt"],
            "display_name": "Mindgrasp | The #1 AI Study Tool for Students",
        }
    }

    markdown = write_assessment_report(metrics, [], str(tmp_path / "r.md"))

    assert "| Mindgrasp \\| The #1 AI Study Tool for Students |" in markdown
    assert "### Mindgrasp | The #1 AI Study Tool for Students" in markdown


def test_the_pdf_uses_display_names(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    out = tmp_path / "r.pdf"
    write_pdf_report(METRICS, write_assessment_report(METRICS, [], str(tmp_path / "r.md")), str(out))
    text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(str(out)).pages)

    assert "Azure AI Search + Azure OpenAI" in text
    assert "ahead of CustomGPT.ai" in text
    assert "azure_ai_search_openai" not in text


def test_a_legacy_run_without_display_names_still_renders(tmp_path):
    """Rows written before this existed must not become blank."""
    legacy = {"tesseract": {"rating": 70, "implementable": True,
                            "assessment_basis": "documentation_evidence",
                            "execution_mode": "comparison_only", "reason": "ok", "evidence": []}}
    markdown = write_assessment_report(legacy, [], str(tmp_path / "r.md"))

    assert "| tesseract |" in markdown
    assert "### tesseract" in markdown
