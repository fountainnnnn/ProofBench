"""Generic documentation-led implementation assessment for Real mode."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

# Documentation assessment plans are model-authored and run unreviewed in a
# sandbox, so they are entitled to no credentials at all. The planner is told
# exactly this set, so a key-dependent integration is rated not-implementable
# instead of being built and then failing on a missing variable.
ASSESSMENT_VERIFICATION_ENTITLEMENTS: frozenset[str] = frozenset()

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

# How a candidate may legitimately be assessed.
#
# ``sandbox_verifiable``: a runnable, safe, credential-free artefact (an
#   open-source library, a documented unauthenticated endpoint, an SDK import
#   check). ProofBench may exercise it in a disposable Daytona sandbox.
# ``comparison_only``: a cloud or SaaS product, anything requiring a paid
#   subscription or a credential ProofBench does not hold, and anything whose
#   documented operations are destructive or otherwise unsafe to invoke. These
#   are compared from bounded documentation evidence. ProofBench must never
#   provision a sandbox for them or imply that execution occurred.
EXECUTION_MODES = frozenset({"sandbox_verifiable", "comparison_only"})
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 90.0
MAX_PROVIDER_TIMEOUT_SECONDS = 900.0


def _provider_timeout_seconds(env: dict[str, str]) -> float:
    """Bound one assessment provider so the fallback chain can make progress."""
    raw = env.get("ASSESSMENT_PROVIDER_TIMEOUT_SECONDS", "")
    try:
        value = float(raw) if raw else DEFAULT_PROVIDER_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_PROVIDER_TIMEOUT_SECONDS
    return min(value, MAX_PROVIDER_TIMEOUT_SECONDS)

# What a persisted rating actually rests on. Rendered verbatim by the report and
# the console, so it must never overstate what happened.
ASSESSMENT_BASES = frozenset({
    "sandbox_execution",       # a real sandbox run produced the verification outcome
    "documentation_evidence",  # scored from documentation alone; nothing was executed
    "unavailable",             # no assessment was produced; scores are withheld
})


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


def _execution_mode(value: dict[str, Any], implementable: bool) -> str:
    """Resolve how this candidate may be assessed.

    An explicit, valid ``execution_mode`` from the model always wins; the prompt
    asks for it on every plan. The inference below is only the legacy path for a
    response that omits it, and it reproduces the previous behaviour: a plan
    claiming implementability is expected to carry runnable verification code,
    and anything else is comparison only.
    """
    declared = str(value.get("execution_mode") or "").strip().casefold()
    if declared in EXECUTION_MODES:
        return declared
    if declared:
        raise ValueError("execution_mode must be sandbox_verifiable or comparison_only")
    return "sandbox_verifiable" if implementable else "comparison_only"


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
    execution_mode = _execution_mode(value, implementable)
    normalized = {
        "implementable": implementable,
        "execution_mode": execution_mode,
        "reason": reason[:600],
        "documentation_quality": _bounded_int(value["documentation_quality"], 0, 100, "documentation_quality"),
        "integration_feasibility": _bounded_int(value["integration_feasibility"], 0, 100, "integration_feasibility"),
        "auth_clarity": _bounded_int(value["auth_clarity"], 0, 100, "auth_clarity"),
        "setup_complexity": _bounded_int(value["setup_complexity"], 1, 5, "setup_complexity"),
        "build_commands": [command.strip() for command in build_commands[:8]],
        "verification_code": verification_code,
        "evidence": [str(item).strip()[:240] for item in evidence[:6] if str(item).strip()],
    }
    if execution_mode == "sandbox_verifiable" and not verification_code:
        raise ValueError("sandbox_verifiable plans require verification_code")
    # Nothing is ever built or executed for a comparison-only or
    # non-implementable candidate, so it carries no runnable payload at all.
    if execution_mode == "comparison_only" or not implementable:
        normalized["build_commands"] = []
        normalized["verification_code"] = ""
    return normalized


def _assessment_prompt(
    tool_name: str,
    docs_text: str,
    objective: str,
    available_credentials: list[str],
) -> str:
    return f"""Assess {tool_name!r} against this company objective: {objective or 'evaluate the documented integration'}.
Base every judgement on the supplied documentation. Do not use unstated prior knowledge.

Return strict JSON with exactly these keys:
{{
  "implementable": true|false,
  "execution_mode": "sandbox_verifiable"|"comparison_only",
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

This list is exhaustive and authoritative: the verification sandbox receives these variables
and nothing else. Any credential not named above is unavailable, however the docs describe it.

execution_mode decides whether anything is actually executed:
- "sandbox_verifiable": the product is a runnable, safe artefact that can be exercised with no
  credentials at all, for example an open-source library, a published package, or a documented
  unauthenticated endpoint. Supply build_commands and verification_code.
- "comparison_only": choose this for a cloud or SaaS product, anything needing a paid plan, an
  account, or any credential not named above, and anything whose documented operations would be
  destructive or otherwise unsafe to invoke. Return empty build_commands and verification_code.
  Nothing will be executed and no sandbox will be provisioned, so do not write code for it.

Scoring is independent of execution_mode. A comparison-only product is scored on the same 0-100
scales purely from documentation evidence, and being unrunnable is NOT a defect: do not lower
documentation_quality, integration_feasibility, or auth_clarity because it needs an account.
Score what the documentation actually shows: completeness, worked examples, error and rate-limit
coverage, and how clearly authentication is specified.

implementable means the documentation is complete enough to build a working integration assuming
its own documented credentials are supplied. Set it false only when the documentation genuinely
cannot support an integration, and say what is missing.

When execution_mode is "sandbox_verifiable", verification_code must be non-destructive, must not
invent endpoints, and must finish by printing PROOFBENCH_OK. It may validate SDK imports, client
construction, or documented unauthenticated behavior. Do not print or embed secrets. Keep install
commands minimal.

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


def _collect(candidates, responses) -> dict[str, dict[str, Any]]:
    assessed: dict[str, dict[str, Any]] = {}
    for item, response in zip(candidates, responses):
        name = item["name"]
        if isinstance(response, BaseException):
            assessed[name] = {"error": f"{type(response).__name__}: request failed"}
            continue
        try:
            content = response.choices[0].message.content
            assessed[name] = {"plan": validate_plan(_extract_json_object(content or ""))}
        except Exception as exc:
            assessed[name] = {"error": f"{type(exc).__name__}: invalid response"}
    return assessed


def assessment_provider(env: dict[str, str] | None = None) -> str:
    """Name the provider that will serve documentation assessment."""
    from engine.llm_clients import resolve_provider

    return resolve_provider("assessment", dict(env or {}))


def assess_documentation_batch(
    candidates: list[dict[str, str]],
    objective: str,
    env: dict[str, str] | None = None,
    entitled_credentials=(),
) -> dict[str, dict[str, Any]]:
    """Assess candidates as one workload on the best configured provider.

    Provider selection is capability based (``engine.llm_clients``): Doubleword's
    native autobatcher when it is configured, otherwise any OpenAI-compatible
    provider such as OpenRouter. If a provider produces nothing usable for any
    candidate we move to the next configured one rather than returning a page of
    zeros, because a provider outage is not evidence about the tools.

    ``entitled_credentials`` must be exactly the set the verification sandbox
    will receive. Advertising anything wider lets the planner build a plan
    around a key that verification can never supply, which then fails for a
    reason unrelated to the tool being assessed. Callers pass the same value
    they hand to ``engine.tools.env_prelude``.
    """
    from engine.llm_clients import capability_providers, provider_chat_completions

    runtime_env = dict(env or {})
    available_credentials = sorted(str(name) for name in entitled_credentials)
    requests = [
        _assessment_request(
            item["name"],
            item["docs_text"],
            objective,
            available_credentials,
        )
        for item in candidates
    ]
    if not requests:
        return {}

    providers = capability_providers("assessment", runtime_env)
    if not providers:
        raise RuntimeError(
            "no assessment provider is configured; set DOUBLEWORD_API_KEY or OPENROUTER_API_KEY"
        )

    assessed: dict[str, dict[str, Any]] = {}
    last_error: Exception | None = None
    provider_timeout = _provider_timeout_seconds(runtime_env)
    for provider in providers:
        try:
            responses = asyncio.run(
                asyncio.wait_for(
                    provider_chat_completions(provider, requests, env=runtime_env),
                    timeout=provider_timeout,
                )
            )
        except Exception as exc:
            last_error = exc
            continue
        assessed = _collect(candidates, responses)
        if any("plan" in result for result in assessed.values()):
            return assessed
    if assessed:
        return assessed
    raise RuntimeError(
        f"every configured assessment provider failed: {type(last_error).__name__}"
        if last_error is not None
        else "every configured assessment provider failed"
    )


def assess_documentation(
    tool_name: str,
    docs_text: str,
    objective: str,
    env: dict[str, str] | None = None,
    entitled_credentials=(),
) -> dict[str, Any]:
    """Assess one tool through the same Doubleword batch path a run uses."""
    result = assess_documentation_batch(
        [{"name": tool_name, "docs_text": docs_text}],
        objective,
        env=env,
        entitled_credentials=entitled_credentials,
    )[tool_name]
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["plan"]


def result_from_plan(
    plan: dict[str, Any],
    verification_status: str,
    daytona_triggered: bool,
) -> dict[str, Any]:
    """Convert a docs plan and optional sandbox outcome into one rating row.

    Suitability is a documentation-evidence score on every path. Sandbox
    execution only adjusts it when execution genuinely happened, so a
    comparison-only product is rated on the same 0-100 scale as a runnable one
    and is never penalised for being unrunnable.
    """
    execution_mode = plan.get("execution_mode", "sandbox_verifiable")
    base = round(
        plan["documentation_quality"] * 0.30
        + plan["integration_feasibility"] * 0.50
        + plan["auth_clarity"] * 0.20
    )
    if not plan["implementable"]:
        # The documentation itself cannot support an integration.
        rating = min(base, 49)
    elif execution_mode == "comparison_only":
        rating = base
    elif verification_status == "passed":
        rating = min(100, base + 10)
    elif verification_status == "failed":
        rating = min(base, 45)
    else:
        rating = base
    executed = bool(daytona_triggered) and verification_status in {"passed", "failed"}
    return {
        "rating": rating,
        "suitability": rating,
        "implementable": bool(plan["implementable"]),
        "execution_mode": execution_mode,
        "assessment_basis": "sandbox_execution" if executed else "documentation_evidence",
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
    """Return a stable row when no assessment could be produced at all.

    Scores are withheld rather than zeroed. A zero is a claim that the tool
    scored badly; a provider outage or a failed scrape is not evidence about the
    tool, and CONTRACTS.md forbids persisting plausible-looking numbers a
    failure did not measure. Callers and the UI render these as unavailable.
    """
    return {
        "rating": None,
        "suitability": None,
        "implementable": None,
        "execution_mode": "comparison_only",
        "assessment_basis": "unavailable",
        "daytona_triggered": False,
        "verification_status": "unavailable",
        "documentation_quality": None,
        "integration_feasibility": None,
        "auth_clarity": None,
        "setup_complexity": None,
        "reason": str(reason)[:600],
        "evidence": [],
    }


_BASIS_LABELS = {
    "sandbox_execution": "Daytona execution",
    "documentation_evidence": "Documentation",
    "unavailable": "Unavailable",
}


def _cell(value) -> str:
    """Render a withheld score honestly instead of printing a fabricated 0."""
    return "n/a" if value is None else str(value)


def _score(values: dict) -> int:
    """Sort key: unscored rows sort last without being rewritten as zero."""
    rating = values.get("rating")
    return rating if isinstance(rating, int) else -1


def write_assessment_report(metrics: dict, citations: list[dict], out_path: str) -> str:
    """Write an evidence-led implementation feasibility report."""
    ranked = sorted(metrics.items(), key=lambda item: -_score(item[1]))
    lines = [
        "# ProofBench Tool Implementation Report",
        "",
        "Suitability is scored from documentation evidence. The basis column states",
        "whether a candidate was executed in a Daytona sandbox or compared from",
        "documentation only. Comparison-only products were never executed.",
        "",
        "## Ranked assessment",
        "",
        "| Rank | Tool | Suitability | Basis | Implementable | Verification | Docs | Feasibility | Auth | Setup |",
        "|---:|---|---:|---|---|---|---:|---:|---:|---:|",
    ]
    for rank, (name, values) in enumerate(ranked, 1):
        rating = values.get("rating")
        implementable = values.get("implementable")
        basis = _BASIS_LABELS.get(values.get("assessment_basis"), "Documentation")
        lines.append(
            f"| {rank} | {name} | "
            f"{'n/a' if rating is None else f'{rating}/100'} | {basis} | "
            f"{'n/a' if implementable is None else ('Yes' if implementable else 'No')} | "
            f"{values.get('verification_status', 'unknown')} | "
            f"{_cell(values.get('documentation_quality'))} | "
            f"{_cell(values.get('integration_feasibility'))} | "
            f"{_cell(values.get('auth_clarity'))} | {_cell(values.get('setup_complexity'))} |"
        )
    lines.extend(["", "## Findings", ""])
    for name, values in ranked:
        if values.get("execution_mode") == "comparison_only":
            note = ("Compared from documentation evidence. This product was not executed, "
                    "so no runtime behaviour is claimed.")
        elif values.get("assessment_basis") == "sandbox_execution":
            note = (f"Executed in a Daytona sandbox; verification "
                    f"{values.get('verification_status', 'unknown')}.")
        else:
            note = "Assessed from documentation evidence."
        lines.extend(
            [
                f"### {name}",
                "",
                values.get("reason") or "No implementation rationale was produced.",
                "",
                note,
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
