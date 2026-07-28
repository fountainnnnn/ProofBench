"""Documentation-evidence scores for candidates a benchmark could not measure.

A benchmark run that could only execute two of six candidates used to publish
four rows of nothing: every quality metric withheld, every cell reading
"Unavailable", and no way to compare the four against anything. Withholding a
measurement that was never taken is right (see ``engine.evaluate``); leaving the
candidate entirely unjudged is not, because the run *had* already read that
product's documentation to build its adapter.

So the same documentation assessment the tool-assessment path runs is applied
here, on its own scale, under its own name. The extraction metrics stay null —
a research score is never allowed to stand in for a measurement — and the row
carries the basis it was scored on so the UI and the report can say which kind
of evidence the reader is looking at.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

# Imported at module scope rather than inside each function: this module is
# itself only imported at evaluation time, so the cost lands in the same place
# and there is one name for a test to substitute.
from engine.tool_assessment import assess_documentation_batch, result_from_plan


# Keys this module owns. Measured extraction metrics are never in this set, so a
# merge can never overwrite a number the sandbox actually produced.
RESEARCH_KEYS = (
    "research_score",
    "research_basis",
    "research_reason",
    "research_evidence",
    "documentation_quality",
    "integration_feasibility",
    "auth_clarity",
    "pricing_transparency",
    "implementable",
)

# Enough for a product's documentation page — the real ones measured here run
# 9k-23k characters — without paying to send a scraped site's whole navigation
# tree through the assessor.
MAX_DOCS_CHARS = 24_000

# This pass runs after the benchmark has already produced its measurements, so
# it can afford to wait where the in-run assessment cannot. A real six-candidate
# batch measured 93s against the 90s default — close enough to the line that one
# slow provider loses the whole pass. An operator's own setting always wins.
RESEARCH_PROVIDER_TIMEOUT_SECONDS = "240"


def extraction_objective(spec: dict[str, Any]) -> str:
    """State what the candidates are being judged against, in one sentence.

    The assessor is asked whether a product can do *this* job, so an extraction
    run has to hand it the job: the fields, from the kind of document the
    dataset holds. Without that it grades documentation in the abstract, where
    every polished marketing page scores well.
    """
    declared = str(spec.get("objective") or "").strip()
    if declared:
        return declared
    category = str(spec.get("category") or "document extraction").replace("_", " ").strip()
    fields = [str(field).strip() for field in (spec.get("fields") or []) if str(field).strip()]
    if not fields:
        return f"Automate {category} from document images."
    return (
        f"Automate {category}: extract {', '.join(fields)} from document images "
        "programmatically, and report the extracted fields as structured data."
    )


def _row_from_plan(plan: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one validated plan into the research half of a metrics row."""
    # "not_applicable": nothing was executed to reach this score, and saying so
    # explicitly keeps result_from_plan from applying an execution bonus or
    # penalty that no execution earned.
    assessed = result_from_plan(plan, "not_applicable", False)
    if assessed.get("rating") is None:
        # Documentation that could not be read at all is not evidence about the
        # product. Withheld, exactly as the assessment path withholds it.
        return None
    return {
        "research_score": assessed["rating"],
        "research_basis": "documentation_evidence",
        "research_reason": assessed["reason"],
        "research_evidence": assessed["evidence"],
        "documentation_quality": assessed["documentation_quality"],
        "integration_feasibility": assessed["integration_feasibility"],
        "auth_clarity": assessed["auth_clarity"],
        "pricing_transparency": assessed["pricing_transparency"],
        "implementable": assessed["implementable"],
        "setup_complexity": assessed["setup_complexity"],
    }


def research_scores(
    candidates: Iterable[dict[str, Any]],
    objective: str,
    env: dict[str, str] | None = None,
    constraints: dict | None = None,
    scrape: Callable[[str], str] | None = None,
    entitled_credentials: Iterable[str] = (),
    on_event: Callable[[str, str], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Score each candidate from its documentation. Never raises.

    A candidate is absent from the result when its documentation could not be
    fetched, the provider returned nothing usable, or no assessment provider is
    configured at all. Absent means unscored, which the caller renders as
    unavailable — the one thing this must never do is invent a number, because
    a fabricated score about a named vendor is the failure this product cannot
    afford.
    """
    fetch = scrape
    if fetch is None:
        from engine.docs_intel import scrape_page

        def fetch(url: str) -> str:
            return scrape_page(url, env=dict(env or {}))

    requests: list[dict[str, str]] = []
    for candidate in candidates:
        name = str(candidate.get("name") or "").strip()
        docs_url = str(candidate.get("docs_url") or "").strip()
        if not name or not docs_url:
            continue
        try:
            docs_text = str(fetch(docs_url))[:MAX_DOCS_CHARS]
        except Exception as exc:
            if on_event:
                on_event(name, f"{type(exc).__name__}: documentation scrape failed")
            continue
        if not docs_text.strip():
            continue
        requests.append({
            "name": name,
            "docs_text": docs_text,
            "role": str(candidate.get("role") or "product"),
        })

    if not requests:
        return {}

    runtime_env = dict(env or {})
    if not str(runtime_env.get("ASSESSMENT_PROVIDER_TIMEOUT_SECONDS") or "").strip():
        runtime_env["ASSESSMENT_PROVIDER_TIMEOUT_SECONDS"] = RESEARCH_PROVIDER_TIMEOUT_SECONDS
    try:
        assessments = assess_documentation_batch(
            requests,
            objective,
            env=runtime_env,
            entitled_credentials=tuple(entitled_credentials),
            constraints=constraints,
        )
    except Exception as exc:
        # A provider outage is not evidence about any of these tools, so the
        # whole pass yields nothing rather than a page of low scores.
        if on_event:
            on_event("*", f"{type(exc).__name__}: research scoring unavailable")
        return {}

    scored: dict[str, dict[str, Any]] = {}
    for name, assessment in (assessments or {}).items():
        plan = (assessment or {}).get("plan")
        if not plan:
            if on_event:
                on_event(name, str((assessment or {}).get("error") or "no assessment returned"))
            continue
        try:
            row = _row_from_plan(plan)
        except Exception:
            row = None
        if row is not None:
            scored[str(name)] = row
    return scored


def merge_research_scores(
    metrics: dict[str, dict[str, Any]],
    scored: dict[str, dict[str, Any]],
    curated_setup: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """Attach research keys to metric rows, in place, without touching measurements.

    ``curated_setup`` names the candidates whose ``setup_complexity`` came from
    the evaluator's own curated table; theirs is kept. Everything else was
    getting the table's default of 1 — a hardcoded number standing in for
    evidence — so the assessed value replaces it.
    """
    curated = {str(name) for name in curated_setup}
    for name, row in (metrics or {}).items():
        if not isinstance(row, dict):
            continue
        research = dict(scored.get(str(name)) or {})
        if not research:
            continue
        setup = research.pop("setup_complexity", None)
        row.update({key: value for key, value in research.items() if key in RESEARCH_KEYS})
        if setup is not None and str(name) not in curated:
            row["setup_complexity"] = setup
    return metrics
