"""Deterministic, clearly labelled artifacts that keep the demo from dead-ending."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Callable


FIELDS = ["invoice_number", "date", "vendor", "total"]
DEFAULT_CANDIDATES = [
    {
        "name": "openai_vision",
        "display_name": "OpenAI Vision",
        "docs_url": "https://platform.openai.com/docs/guides/vision",
        "use_fallback": True,
    },
    {
        "name": "doubleword",
        "display_name": "Doubleword DeepSeek V4 Pro",
        "docs_url": "https://docs.doubleword.ai",
        "use_fallback": True,
    },
    {
        "name": "easyocr",
        "display_name": "EasyOCR",
        "docs_url": "https://github.com/JaidedAI/EasyOCR",
        "use_fallback": True,
    },
    {
        "name": "tesseract",
        "display_name": "Tesseract OCR",
        "docs_url": "https://tesseract-ocr.github.io/tessdoc/",
        "use_fallback": True,
    },
]

_PRESETS = {
    "openai_vision": (0.967, 0.975, 0.012, 1.18, 0.0, 7.50, 1),
    "doubleword": (0.942, 0.956, 0.021, 2.41, 0.0, 2.10, 2),
    "easyocr": (0.825, 0.861, 0.084, 0.74, 0.033, 0.0, 3),
    "tesseract": (0.775, 0.814, 0.112, 0.31, 0.067, 0.0, 2),
    "nosana_vlm": (0.925, 0.941, 0.028, 1.67, 0.0, 3.40, 2),
}

# Demo metrics are deterministic local presets. Keep the run itself immediate;
# the separate intake trace owns the deliberate 2.2-second discovery cadence.
# Set this environment variable only when a longer run walkthrough is desired.
DEMO_STAGE_DELAY_S = float(os.environ.get("DEMO_STAGE_DELAY_S", "0"))


class DemoRunCancelled(RuntimeError):
    """Raised when a user stops the guided demo before it completes."""


def demo_spec(dataset_path: str, request: str = "") -> dict:
    return {
        "task": "invoice field extraction",
        "category": "Document intelligence",
        "dataset": {"path": dataset_path},
        "fields": list(FIELDS),
        "candidates": [dict(candidate) for candidate in DEFAULT_CANDIDATES],
        "ranking_metric": "exact_accuracy",
        "demo_mode": True,
        "demo_note": "Prepared locally because live discovery was unavailable.",
        "request_summary": request[:160],
    }


def _unknown_preset(name: str) -> tuple[float, float, float, float, float, float, int]:
    seed = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
    exact = 0.78 + (seed % 150) / 1000
    return (
        round(exact, 3),
        round(min(0.98, exact + 0.032), 3),
        round(max(0.015, 0.15 - exact / 8), 3),
        round(0.45 + (seed % 190) / 100, 2),
        round((seed % 30) / 1000, 3),
        round((seed % 700) / 100, 2),
        2 + seed % 3,
    )


def demo_metrics(spec: dict, n_docs: int = 15) -> dict:
    metrics = {}
    candidates = spec.get("candidates") or DEFAULT_CANDIDATES
    for candidate in candidates:
        name = str(candidate.get("name") or "candidate")
        exact, f1, cer, latency, failure, cost, setup = _PRESETS.get(
            name, _unknown_preset(name)
        )
        metrics[name] = {
            "exact_accuracy": exact,
            "field_f1": f1,
            "cer": cer,
            "mean_latency_s": latency,
            "failure_rate": failure,
            "cost_per_1k_docs": cost,
            "setup_complexity": setup,
            "n_docs": n_docs,
            "is_demo": True,
        }
    return metrics


def demo_report(metrics: dict) -> str:
    ranked = sorted(
        metrics.items(),
        key=lambda item: (-item[1]["exact_accuracy"], -item[1]["field_f1"]),
    )
    lines = [
        "# ProofBench Benchmark Report",
        "",
        "> **Deterministic run:** These representative metrics are generated locally. "
        "Use Real mode to replace them with measured results.",
        "",
        "## Executive summary",
        "",
    ]
    if ranked:
        winner, winner_metrics = ranked[0]
        lines.append(
            f"**{winner}** leads this demo comparison at "
            f"{winner_metrics['exact_accuracy'] * 100:.1f}% exact accuracy. "
            "Hosted vision models provide the strongest extraction quality, while local OCR "
            "offers lower cost and latency."
        )
    lines.extend(
        [
            "",
            "## Ranked results",
            "",
            "| Rank | Candidate | Exact accuracy | Field F1 | CER | Latency | Failure rate | Cost / 1k |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for rank, (name, values) in enumerate(ranked, 1):
        lines.append(
            f"| {rank} | {name} | {values['exact_accuracy'] * 100:.1f}% | "
            f"{values['field_f1'] * 100:.1f}% | {values['cer']:.3f} | "
            f"{values['mean_latency_s']:.2f}s | {values['failure_rate'] * 100:.1f}% | "
            f"${values['cost_per_1k_docs']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            "Use the leading hosted model when accuracy is the priority. Keep Tesseract or "
            "EasyOCR as a low-cost baseline and operational fallback.",
            "",
            "## Methodology",
            "",
            "This mode uses deterministic local metrics for a fast, repeatable walkthrough. "
            "Use Real mode for measured provider results.",
            "",
        ]
    )
    return "\n".join(lines)


def emit_demo_run(
    spec: dict,
    emit: Callable[[str, dict], None],
    run_dir: str,
    n_docs: int = 15,
    step_delay_s: float | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict, str]:
    """Emit a complete, polished event sequence and persist its artifacts."""
    candidate_configs = spec.get("candidates", [])
    candidates = [str(c.get("name") or "candidate") for c in candidate_configs]
    if not candidates:
        candidates = ["openai_vision", "tesseract"]
    display_names = {
        str(candidate.get("name") or "candidate"): str(
            candidate.get("display_name") or candidate.get("name") or "candidate"
        )
        for candidate in candidate_configs
    }

    def sandbox_update(name: str, line: str, phase: str, chat_detail: str) -> None:
        """Mirror each simulated sandbox step in both the trace and chat transcript."""
        emit("artifact", {
            "kind": "sandbox_log",
            "sandbox": name,
            "line": line,
            "phase": phase,
        })
        emit("delta", {
        "text": f"- **{display_names.get(name, name)}**: {chat_detail}\n",
        })

    def pause(multiplier: float = 1.0) -> None:
        if should_stop and should_stop():
            raise DemoRunCancelled()
        delay = DEMO_STAGE_DELAY_S if step_delay_s is None else step_delay_s
        # Short slices make Stop responsive without making the trace appear instant.
        remaining = max(0.0, delay * multiplier)
        while remaining:
            interval = min(0.04, remaining)
            time.sleep(interval)
            remaining -= interval
            if should_stop and should_stop():
                raise DemoRunCancelled()

    emit("delta", {"text": "Benchmark run prepared. The trace below shows the full workflow.\n\n### Sandbox progress\n"})
    pause(0.75)
    emit("artifact", {
        "kind": "trace",
        "tool": "demo_fallback",
        "args_summary": "deterministic representative run",
        "status": "ok",
        "detail": "Deterministic local metrics loaded for the benchmark run.",
    })
    pause()
    emit("state", {"phase": "DOCS_INTEL", "candidates": {name: "queued" for name in candidates}})
    for name in candidates:
        emit("artifact", {
            "kind": "trace", "tool": "web_search",
            "args_summary": f"{name} invoice extraction documentation",
            "status": "ok", "detail": "Candidate discovered and ranked for compatibility.",
        })
        emit("artifact", {
            "kind": "trace", "tool": "scrape_docs",
            "args_summary": f"{name} API, installation, and pricing pages",
            "status": "ok", "detail": "Documentation indexed for adapter generation.",
        })
        pause(0.25)
    emit("state", {"phase": "ADAPTER_GEN", "candidates": {name: "generating" for name in candidates}})
    pause()
    for name in candidates:
        emit("artifact", {
            "kind": "trace", "tool": "generate_adapter",
            "args_summary": name, "status": "ok",
            "detail": "Extraction adapter generated and contract checked.",
        })
        pause(0.25)
    emit("state", {"phase": "PROVISIONING", "candidates": {name: "ready" for name in candidates}})
    pause()
    for name in candidates:
        sandbox_update(
            name,
            "Sandbox allocated and dataset staged",
            "building",
            "sandbox allocated, labelled invoice dataset staged",
        )
        pause(0.25)
    emit("state", {"phase": "BUILDING", "candidates": {name: "building" for name in candidates}})
    pause()
    for name in candidates:
        sandbox_update(
            name,
            "Installing adapter dependencies and compiling extraction runner",
            "building",
            "adapter dependencies installed and extraction runner prepared",
        )
        pause(0.25)
    emit("state", {"phase": "VALIDATING", "candidates": {name: "validating" for name in candidates}})
    pause()
    for name in candidates:
        sandbox_update(
            name,
            "Validation sample passed structured field contract",
            "validating",
            "validation sample passed the structured field contract",
        )
        pause(0.25)
    emit("state", {"phase": "RUNNING", "candidates": {name: "running" for name in candidates}})
    pause()
    for name in candidates:
        sandbox_update(
            name,
            f"Processed {n_docs}/{n_docs} documents",
            "running",
            f"processed {n_docs}/{n_docs} documents and returned structured fields",
        )
        pause(0.25)
    emit("state", {"phase": "COLLATING", "candidates": {name: "collating" for name in candidates}})
    pause()
    emit("artifact", {
        "kind": "trace", "tool": "collate_results",
        "args_summary": f"{len(candidates)} candidate result streams",
        "status": "ok", "detail": "Normalized extraction payloads and paired them with ground truth.",
    })
    pause()
    emit("state", {"phase": "EVALUATING", "candidates": {name: "done" for name in candidates}})
    pause()
    metrics = demo_metrics(spec, n_docs=n_docs)
    report = demo_report(metrics)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "metrics.json"), "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, indent=2)
    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(report)
    from engine.pdf_report import write_pdf_report

    write_pdf_report(metrics, report, os.path.join(run_dir, "report.pdf"))
    emit("artifact", {"kind": "results", "metrics": metrics, "demo_mode": True})
    emit("state", {"phase": "REPORTING", "candidates": {}})
    pause()
    emit("artifact", {
        "kind": "report",
        "markdown": report,
        "citations": [],
        "demo_mode": True,
    })
    emit("state", {"phase": "DONE", "candidates": {}})
    return metrics, report
