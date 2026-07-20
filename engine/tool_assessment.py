"""Generic documentation-led implementation assessment for Real mode."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any


REQUIRED_PLAN_KEYS = {
    "implementable",
    "reason",
    "documentation_quality",
    "integration_feasibility",
    "auth_clarity",
    "setup_complexity",
    "build_commands",
    "verification_code",
    "evidence",
}


def _extract_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("assessment response must be a JSON object")
    return value


def _bounded_int(value: Any, low: int, high: int, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not low <= number <= high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return number


def validate_plan(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one model-produced implementation plan."""
    missing = REQUIRED_PLAN_KEYS.difference(value)
    if missing:
        raise ValueError(f"assessment response missing: {', '.join(sorted(missing))}")
    implementable = value["implementable"]
    if not isinstance(implementable, bool):
        raise ValueError("implementable must be boolean")
    reason = str(value["reason"]).strip()
    if not reason:
        raise ValueError("reason is required")
    build_commands = value["build_commands"]
    if not isinstance(build_commands, list) or not all(
        isinstance(command, str) and command.strip() for command in build_commands
    ):
        raise ValueError("build_commands must be a list of non-empty strings")
    verification_code = str(value["verification_code"] or "").strip()
    evidence = value["evidence"]
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    normalized = {
        "implementable": implementable,
        "reason": reason[:600],
        "documentation_quality": _bounded_int(value["documentation_quality"], 0, 100, "documentation_quality"),
        "integration_feasibility": _bounded_int(value["integration_feasibility"], 0, 100, "integration_feasibility"),
        "auth_clarity": _bounded_int(value["auth_clarity"], 0, 100, "auth_clarity"),
        "setup_complexity": _bounded_int(value["setup_complexity"], 1, 5, "setup_complexity"),
        "build_commands": [command.strip() for command in build_commands[:8]],
        "verification_code": verification_code,
        "evidence": [str(item).strip()[:240] for item in evidence[:6] if str(item).strip()],
    }
    if implementable and not verification_code:
        raise ValueError("implementable plans require verification_code")
    if not implementable:
        normalized["build_commands"] = []
        normalized["verification_code"] = ""
    return normalized


def _assessment_prompt(
    tool_name: str,
    docs_text: str,
    objective: str,
    available_credentials: list[str],
) -> str:
    return f"""Assess whether {tool_name!r} can be implemented from the supplied documentation
for this company objective: {objective or 'evaluate the documented integration'}.

Return strict JSON with exactly these keys:
{{
  "implementable": true|false,
  "reason": "concise evidence-based reason",
  "documentation_quality": 0-100,
  "integration_feasibility": 0-100,
  "auth_clarity": 0-100,
  "setup_complexity": 1-5,
  "build_commands": ["commands supported by the docs"],
  "verification_code": "Python smoke-test source",
  "evidence": ["specific documented facts"]
}}

Available credential variable names (values are intentionally hidden):
{', '.join(available_credentials) if available_credentials else '(none)'}

Set implementable=false only when the documented integration requires a paid subscription,
or it requires an API key/token that is not present in the available credential names above.
In that case return empty build_commands and verification_code and explain which requirement
blocked implementation. Otherwise produce the best credible implementation supported by the docs.
When implementable=true, verification_code must be non-destructive, must not invent endpoints,
and must finish by printing PROOFBENCH_OK. It may validate SDK imports, client construction, or
documented unauthenticated behavior. Do not print or embed secrets. Keep install commands minimal.

DOCUMENTATION:
{docs_text[:24000]}
"""


def _assessment_request(
    tool_name: str,
    docs_text: str,
    objective: str,
    available_credentials: list[str],
) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": "You assess implementation feasibility from documentation. Return strict JSON only."},
            {
                "role": "user",
                "content": _assessment_prompt(
                    tool_name,
                    docs_text,
                    objective,
                    available_credentials,
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


def assess_documentation_batch(
    candidates: list[dict[str, str]],
    objective: str,
    env: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Assess all Real-mode candidates in one Doubleword autobatcher workload."""
    from engine.llm_clients import batch_chat_completions

    runtime_env = dict(os.environ)
    runtime_env.update(env or {})
    model = runtime_env.get("DOUBLEWORD_MODEL", "deepseek-ai/DeepSeek-V4-Pro")
    available_credentials = sorted(
        name
        for name, value in runtime_env.items()
        if value and re.search(r"(?:API_KEY|TOKEN|SECRET|PASSWORD)$", name)
    )
    requests = [
        _assessment_request(
            item["name"],
            item["docs_text"],
            objective,
            available_credentials,
        )
        for item in candidates
    ]
    responses = asyncio.run(
        batch_chat_completions(requests, model=model, env=runtime_env)
    )
    assessed: dict[str, dict[str, Any]] = {}
    for item, response in zip(candidates, responses):
        name = item["name"]
        if isinstance(response, BaseException):
            assessed[name] = {"error": f"{type(response).__name__}: {response}"}
            continue
        try:
            content = response.choices[0].message.content
            assessed[name] = {"plan": validate_plan(_extract_json_object(content or ""))}
        except Exception as exc:
            assessed[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return assessed


def assess_documentation(
    tool_name: str,
    docs_text: str,
    objective: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assess one tool through the same Doubleword batch path used by Real mode."""
    result = assess_documentation_batch(
        [{"name": tool_name, "docs_text": docs_text}],
        objective,
        env=env,
    )[tool_name]
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["plan"]


def result_from_plan(
    plan: dict[str, Any],
    verification_status: str,
    daytona_triggered: bool,
) -> dict[str, Any]:
    """Convert a docs plan and optional sandbox outcome into one rating row."""
    base = round(
        plan["documentation_quality"] * 0.30
        + plan["integration_feasibility"] * 0.50
        + plan["auth_clarity"] * 0.20
    )
    if not plan["implementable"]:
        rating = min(base, 49)
    elif verification_status == "passed":
        rating = min(100, base + 10)
    elif verification_status == "failed":
        rating = min(base, 45)
    else:
        rating = base
    return {
        "rating": rating,
        "implementable": bool(plan["implementable"]),
        "daytona_triggered": bool(daytona_triggered),
        "verification_status": verification_status,
        "documentation_quality": plan["documentation_quality"],
        "integration_feasibility": plan["integration_feasibility"],
        "auth_clarity": plan["auth_clarity"],
        "setup_complexity": plan["setup_complexity"],
        "reason": plan["reason"],
        "evidence": plan["evidence"],
    }


def unavailable_result(reason: str) -> dict[str, Any]:
    """Return a stable result when documentation cannot support implementation."""
    return {
        "rating": 0,
        "implementable": False,
        "daytona_triggered": False,
        "verification_status": "not_implementable",
        "documentation_quality": 0,
        "integration_feasibility": 0,
        "auth_clarity": 0,
        "setup_complexity": 5,
        "reason": str(reason)[:600],
        "evidence": [],
    }


def write_assessment_report(metrics: dict, citations: list[dict], out_path: str) -> str:
    """Write an evidence-led implementation feasibility report."""
    ranked = sorted(metrics.items(), key=lambda item: -int(item[1].get("rating", 0)))
    lines = [
        "# ProofBench Tool Implementation Report",
        "",
        "## Ranked assessment",
        "",
        "| Rank | Tool | Rating | Implementable | Daytona | Verification | Docs | Feasibility | Auth | Setup |",
        "|---:|---|---:|---|---|---|---:|---:|---:|---:|",
    ]
    for rank, (name, values) in enumerate(ranked, 1):
        lines.append(
            f"| {rank} | {name} | {values.get('rating', 0)}/100 | "
            f"{'Yes' if values.get('implementable') else 'No'} | "
            f"{'Used' if values.get('daytona_triggered') else 'Skipped'} | "
            f"{values.get('verification_status', 'unknown')} | "
            f"{values.get('documentation_quality', 0)} | "
            f"{values.get('integration_feasibility', 0)} | "
            f"{values.get('auth_clarity', 0)} | {values.get('setup_complexity', 5)} |"
        )
    lines.extend(["", "## Findings", ""])
    for name, values in ranked:
        lines.extend(
            [
                f"### {name}",
                "",
                values.get("reason") or "No implementation rationale was produced.",
                "",
            ]
        )
        evidence = values.get("evidence") or []
        if evidence:
            lines.extend([f"- {item}" for item in evidence])
            lines.append("")
    lines.extend(["## Sources", ""])
    if citations:
        for citation in citations:
            title = citation.get("title") or citation.get("url") or "Documentation"
            url = citation.get("url") or ""
            lines.append(f"- [{title}]({url})" if url else f"- {title}")
    else:
        lines.append("- No documentation page was available.")
    markdown = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    return markdown
