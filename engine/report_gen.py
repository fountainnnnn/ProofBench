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


def _rank(metrics: dict) -> list[tuple[str, dict]]:
    """Rank candidates: higher exact_accuracy first, then F1, then lower latency."""
    def key(item):
        _, m = item
        return (
            -float(m.get("exact_accuracy", 0) or 0),
            -float(m.get("field_f1", 0) or 0),
            float(m.get("mean_latency_s", 0) or 0),
        )
    return sorted(metrics.items(), key=key)


def _fallback_report(metrics: dict, citations: list[dict]) -> str:
    """Deterministic markdown table built from metrics only (no LLM)."""
    ranked = _rank(metrics)
    lines = ["# ProofBench Report", "", "## Summary", ""]
    header = "| Rank | Candidate | " + " | ".join(label for _, label in _COLUMNS) + " |"
    sep = "|---|---|" + "|".join("---" for _ in _COLUMNS) + "|"
    lines.append(header)
    lines.append(sep)
    for i, (name, m) in enumerate(ranked, 1):
        cells = " | ".join(_fmt(m.get(k)) for k, _ in _COLUMNS)
        lines.append(f"| {i} | {name} | {cells} |")
    lines.append("")
    if ranked:
        winner = ranked[0][0]
        lines.append(f"## Verdict\n\n**{winner}** ranks first by exact accuracy across "
                     f"{ranked[0][1].get('n_docs', 'n')} documents.")
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
    return (
        "You are a benchmarking report writer. Produce a ranked markdown report from the "
        "metrics below. You MUST NOT invent, estimate, or alter any number — only reformat "
        "the values given. Structure exactly:\n"
        "1. A summary markdown table ranked best-first with columns: Candidate, Exact accuracy, "
        "F1, CER, Latency (s), Failure rate, Cost/1k docs, Setup complexity.\n"
        "2. A per-candidate findings section (one short paragraph each).\n"
        "3. A 2-3 sentence verdict naming the winner.\n"
        "4. A 'Sources' section listing the citations as markdown links.\n\n"
        "Ranking priority: higher exact accuracy, then higher F1, then lower latency.\n\n"
        f"METRICS JSON:\n{json.dumps(metrics, indent=2)}\n\n"
        f"CITATIONS JSON:\n{json.dumps(citations, indent=2)}\n"
    )


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
    try:
        from engine.llm_clients import chat_client, provider_model, resolve_provider

        # Capability based: Moonshot, then OpenAI, then OpenRouter. A deployment
        # holding only OPENROUTER_API_KEY writes its reports on OpenRouter.
        provider = resolve_provider("report", runtime_env)
        client = chat_client(provider, runtime_env)
        model = provider_model(provider, runtime_env)
        if provider == "openai":
            model = runtime_env.get("OPENAI_REPORT_MODEL", "").strip() or model
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise report writer. Never invent numbers."},
                {"role": "user", "content": _build_prompt(metrics, citations)},
            ],
        )
        markdown = resp.choices[0].message.content
        if not markdown or not markdown.strip():
            raise ValueError("empty completion")
    except Exception as e:
        print(f"[report_gen] LLM report failed, using fallback: {type(e).__name__}",
              file=sys.stderr)
        markdown = _fallback_report(metrics, citations)

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return markdown
