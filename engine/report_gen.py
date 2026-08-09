"""CLAUDE lane — CONTRACTS §10. Ranked markdown report writer (Kimi, with fallback)."""
from __future__ import annotations

import json
import os
import sys

# Column order for the summary table (CONTRACTS §10).
_COLUMNS = [
    ("exact_accuracy", "Exact accuracy"),
    ("field_f1", "F1"),
    ("cer", "CER"),
    ("mean_latency_s", "Latency (s)"),
    ("failure_rate", "Failure rate"),
    ("cost_per_1k_docs", "Cost/1k docs"),
    ("setup_complexity", "Setup complexity"),
]


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _scored(metrics: dict) -> bool:
    """True when a candidate produced at least one result to score."""
    def measured(m: dict) -> bool:
        return m.get("status", "ok") != "no_result" and m.get("exact_accuracy") is not None
    return measured(metrics)


def _partition(metrics: dict) -> tuple[dict, dict]:
    """Split candidates into those that were measured and those that never ran."""
    measured = {name: m for name, m in (metrics or {}).items() if _scored(m)}
    unmeasured = {name: m for name, m in (metrics or {}).items() if not _scored(m)}
    return measured, unmeasured


def _rank(metrics: dict) -> list[tuple[str, dict]]:
    """Rank candidates: higher exact_accuracy first, then F1, then lower latency."""
    def key(item):
        name, m = item
        return (
            -float(m.get("exact_accuracy", 0) or 0),
            -float(m.get("field_f1", 0) or 0),
            float(m.get("mean_latency_s", 0) or 0),
            name,
        )
    return sorted(metrics.items(), key=key)


def _research_note(m: dict) -> str:
    """Render the documentation-evidence score, on its own terms.

    Named as documentation throughout: it is what the docs support, not what the
    product scored on this dataset. Absent when the documentation could not be
    read, because an unscored candidate is not a zero.
    """
    score = m.get("research_score")
    if score is None:
        return ""
    axes = [
        ("docs", m.get("documentation_quality")),
        ("integration", m.get("integration_feasibility")),
        ("auth", m.get("auth_clarity")),
    ]
    detail = ", ".join(f"{label} {value}" for label, value in axes if value is not None)
    note = f" Documentation score {score}/100"
    return f"{note} ({detail})." if detail else f"{note}."


def _fallback_report(metrics: dict, citations: list[dict]) -> str:
    """Deterministic markdown table built from metrics only (no LLM)."""
    measured, unmeasured = _partition(metrics)
    ranked = _rank(measured)
    lines = ["# ProofBench Report", "", "## Summary", ""]
    header = "| Rank | Candidate | " + " | ".join(label for _, label in _COLUMNS) + " |"
    sep = "|---|---|" + "|".join("---" for _ in _COLUMNS) + "|"
    lines.append(header)
    lines.append(sep)
    for i, (name, m) in enumerate(ranked, 1):
        cells = " | ".join(_fmt(m.get(k)) for k, _ in _COLUMNS)
        safe_name = str(name).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {safe_name} | {cells} |")
    lines.append("")
    if unmeasured:
        lines.append("## Did not run")
        lines.append("")
        lines.append("These candidates produced no result to score. They are not ranked, "
                     "and no accuracy is claimed for them. Where their documentation could "
                     "be read, a documentation-evidence score is given: it says how "
                     "implementable the product looks, never how well it performs.")
        lines.append("")
        for name, m in sorted(unmeasured.items()):
            reason = str(m.get("error_summary") or "no result was produced").strip()
            lines.append(f"- **{name}**: {reason}{_research_note(m)}")
        lines.append("")
    if ranked:
        winner = ranked[0][0]
        lines.append(f"## Verdict\n\n**{winner}** ranks first by exact accuracy across "
                     f"{ranked[0][1].get('n_docs', 'n')} documents.")
        if unmeasured:
            lines.append("")
            lines.append(f"This verdict covers the {len(ranked)} candidate(s) that ran. "
                         f"{len(unmeasured)} candidate(s) could not be evaluated.")
    else:
        lines.append("## Verdict\n\nNo candidate produced a result, so this run has no "
                     "winner. Resolve the failures listed above and run the benchmark again.")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    if citations:
        for c in citations:
            title = c.get("title") or c.get("url") or "source"
            url = c.get("url") or ""
            lines.append(f"- [{title}]({url})" if url else f"- {title}")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def _build_prompt(metrics: dict, citations: list[dict]) -> str:
    measured, unmeasured = _partition(metrics)
    verdict_rule = (
        "3. A 2-3 sentence verdict naming the winner among the ranked candidates only.\n"
        if measured
        else "3. A verdict stating plainly that no candidate produced a result, so the run "
             "has no winner and no comparison can be drawn.\n"
    )
    unmeasured_rule = ""
    if unmeasured:
        names = ", ".join(sorted(unmeasured))
        unmeasured_rule = (
            f"\nCRITICAL: these candidates produced no result at all: {names}. "
            "They were NOT measured. Do not rank them, do not put them in the summary table, "
            "do not describe them as scoring zero, performing poorly, or tying with anything. "
            "Instead add a section titled 'Did not run' that names each one and quotes its "
            "error_summary verbatim as the reason.\n"
            "Where a candidate carries research_score, report it in that section as a "
            "documentation-evidence score out of 100 — what its documentation supports, "
            "never a benchmark result — and you may quote research_reason. A candidate "
            "with no research_score has none; do not invent one.\n"
        )
    return (
        "You are a benchmarking report writer. Produce a ranked markdown report from the "
        "metrics below. You MUST NOT invent, estimate, or alter any number — only reformat "
        "the values given. A null value means the metric was not measured; render it as "
        "'n/a' and never as 0. Structure exactly:\n"
        "1. A summary markdown table ranked best-first with columns: Candidate, Exact accuracy, "
        "F1, CER, Latency (s), Failure rate, Cost/1k docs, Setup complexity.\n"
        "2. A per-candidate findings section (one short paragraph each).\n"
        + verdict_rule +
        "4. A 'Sources' section listing the citations as markdown links.\n\n"
        "Ranking priority: higher exact accuracy, then higher F1, then lower latency.\n"
        + unmeasured_rule + "\n"
        f"RANKED METRICS JSON:\n{json.dumps(measured, indent=2)}\n\n"
        f"UNMEASURED CANDIDATES JSON:\n{json.dumps(unmeasured, indent=2)}\n\n"
        f"CITATIONS JSON:\n{json.dumps(citations, indent=2)}\n"
    )


def _compose(provider: str, metrics: dict, citations: list[dict],
             runtime_env: dict[str, str]) -> str:
    """Ask one provider for the narrative report. Raises if it does not answer.

    An empty reply gets ONE retry with the failure fed back, the way assessment
    validation failures are. Failing the provider on the first blank meant a
    transient hiccup at the very last stage discarded a provider that was
    otherwise healthy — and this stage runs after the sandbox spend is sunk.
    """
    from engine.llm_clients import chat_client, provider_model

    model = provider_model(provider, runtime_env)
    if provider == "openai":
        model = runtime_env.get("OPENAI_REPORT_MODEL", "").strip() or model
    client = chat_client(provider, runtime_env)
    prompt = _build_prompt(metrics, citations)
    for _attempt in range(2):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise report writer. Never invent numbers."},
                {"role": "user", "content": prompt},
            ],
        )
        markdown = resp.choices[0].message.content
        if markdown and markdown.strip():
            return markdown
        prompt += (
            "\n\nIMPORTANT: a previous attempt at this report returned an empty "
            "reply. Return the full markdown report, never an empty message."
        )
    raise ValueError("empty completion twice")


def write_report(
    metrics: dict,
    citations: list[dict],
    out_path: str,
    env: dict[str, str] | None = None,
) -> str:
    """Generate a ranked markdown report, write it to out_path, return the markdown."""
    citations = citations or []
    markdown = None
    runtime_env = dict(env or {})
    # Every configured provider is tried in preference order. Resolving only the
    # first one meant a single provider being rate limited silently downgraded
    # every report to the bare table, even with a healthy provider configured.
    try:
        from engine.llm_clients import capability_providers

        providers = capability_providers("report", runtime_env)
    except Exception:
        providers = ()
    for provider in providers:
        try:
            markdown = _compose(provider, metrics, citations, runtime_env)
            break
        except Exception as exc:
            print(f"[report_gen] report provider {provider} failed: {type(exc).__name__}",
                  file=sys.stderr)
    if not markdown:
        tried = ", ".join(providers) if providers else "none configured"
        print(f"[report_gen] every report provider failed ({tried}), "
              "using the deterministic table", file=sys.stderr)
        markdown = _fallback_report(metrics, citations)

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return markdown
