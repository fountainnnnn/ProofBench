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
# Full passes over the provider list before an unresolved candidate is reported
# failed. Two, not more: the second sweep exists to retry validation failures
# with the error fed back, and a reply that is still malformed after every
# provider has seen the correction is evidence, not noise.
MAX_ASSESSMENT_SWEEPS = 2


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
    # Pricing is optional on purpose and stays out of REQUIRED_PLAN_KEYS: a
    # provider that answers without it is still a valid plan, and an absent or
    # explicitly null score means the supplied text disclosed no pricing at all.
    # That is withheld evidence, not a low score.
    raw_pricing = value.get("pricing_transparency")
    pricing = (
        None if raw_pricing is None
        else _bounded_int(raw_pricing, 0, 100, "pricing_transparency")
    )
    normalized = {
        "implementable": implementable,
        "execution_mode": execution_mode,
        "reason": reason[:600],
        "documentation_quality": _bounded_int(value["documentation_quality"], 0, 100, "documentation_quality"),
        "integration_feasibility": _bounded_int(value["integration_feasibility"], 0, 100, "integration_feasibility"),
        "auth_clarity": _bounded_int(value["auth_clarity"], 0, 100, "auth_clarity"),
        "pricing_transparency": pricing,
        "pricing_notes": str(value.get("pricing_notes") or "").strip()[:300],
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


def format_constraints(constraints: dict | None) -> str:
    """Render the user's stated constraints for the assessment prompt.

    Only ever built from the intake spec's bounded constraint object; an empty
    or absent object renders as nothing at all, so the prompt never implies the
    user stated requirements they did not.
    """
    if not isinstance(constraints, dict) or not constraints:
        return ""
    parts: list[str] = []
    stack = constraints.get("stack")
    if isinstance(stack, list) and stack:
        parts.append("Existing stack: " + ", ".join(str(item) for item in stack))
    must_have = constraints.get("must_have")
    if isinstance(must_have, list) and must_have:
        parts.append("Hard requirements: " + ", ".join(str(item) for item in must_have))
    budget = str(constraints.get("budget") or "").strip()
    if budget:
        parts.append(f"Budget or scale: {budget}")
    deployment = str(constraints.get("deployment") or "").strip()
    if deployment:
        parts.append(f"Deployment: {deployment}")
    return "\n".join(parts)


def _assessment_prompt(
    tool_name: str,
    docs_text: str,
    objective: str,
    available_credentials: list[str],
    constraints_text: str = "",
    pricing_text: str = "",
    candidate_role: str = "product",
) -> str:
    constraints_block = (
        "\nThe company stated these constraints. Judge integration_feasibility and "
        "setup_complexity against this specific environment, not in the abstract:\n"
        f"{constraints_text}\n"
        if constraints_text
        else ""
    )
    header = (
        f"Assess {tool_name!r} against this company objective: "
        f"{objective or 'evaluate the documented integration'}.\n"
        "Base every judgement on the supplied documentation. Do not use unstated prior knowledge.\n"
    )
    if candidate_role == "build_component":
        # A component is one part of a self-built integration. Judged against
        # the whole objective, SymPy fails for not drawing diagrams and
        # Matplotlib fails for not writing questions — every part fails for not
        # being the whole, and the build path can never have a viable member.
        #
        # The role rule alone did not hold: stated after the objective and the
        # must-have list, it lost to them. Nodemailer failed for "no webhook
        # support", and Redis, RabbitMQ and Beanstalkd all failed for "no
        # scheduled jobs / retry with backoff" — capabilities belonging to the
        # framework layer above them. So the framing now comes first, before the
        # objective is ever stated, and the constraints carry it too.
        header = (
            f"{tool_name!r} is a BUILD COMPONENT: ONE PART of a self-built integration, to be "
            "composed with other parts that supply the rest of the build. Assess this one part, "
            "never the finished system.\n"
            "The finished build serves this company objective: "
            f"{objective or 'evaluate the documented integration'}.\n"
            "Base every judgement on the supplied documentation. Do not use unstated prior knowledge.\n"
        )
        if constraints_text:
            constraints_block = (
                "\nThe company stated these constraints. Judge integration_feasibility and "
                "setup_complexity against this specific environment, not in the abstract:\n"
                f"{constraints_text}\n"
                "These constraints and hard requirements describe the FINISHED system, not this "
                "one part. This part is not required to satisfy them alone, and must never be "
                "failed for lacking a capability another part of the build would supply.\n"
            )
        capability_rule = (
            "This candidate is a BUILD COMPONENT: one part of a self-built integration, to be "
            "composed with other components. Read the objective for the capabilities it "
            "requires, and pass this test if the documentation shows the component fulfils AT "
            "LEAST ONE of them — generating the content, or rendering the named output form, or "
            "another required part. It fails only when the documentation shows it contributes "
            "to none of the objective's required capabilities. Never fail a component for not "
            "covering the parts of the objective that other components would cover. When it "
            "passes, the reason must name which required capability this part covers; when it "
            "fails, the reason must state that it contributes to none of them, never that it "
            "lacks one specific capability."
        )
    else:
        capability_rule = (
            "Read the objective for the capabilities it requires — an output type, a format, a "
            "feature it names. If the supplied documentation shows no evidence the tool does "
            "one of them, implementable is false and the reason must name the missing "
            "capability. This holds however well documented, well maintained, or easy to "
            "install the tool is: being trivially integrable is not evidence of doing the job. "
            "A library that generates the right kind of thing but not in the form the "
            "objective asked for fails this test."
        )
    return f"""{header}{constraints_block}
Return strict JSON with exactly these keys:
{{
  "implementable": true|false,
  "execution_mode": "sandbox_verifiable"|"comparison_only",
  "reason": "concise evidence-based reason",
  "documentation_quality": 0-100,
  "integration_feasibility": 0-100,
  "auth_clarity": 0-100,
  "pricing_transparency": 0-100 or null,
  "pricing_notes": "one sentence on documented pricing, or empty",
  "setup_complexity": 1-5,
  "build_commands": ["commands supported by the docs"],
  "verification_code": "Python smoke-test source",
  "evidence": ["specific documented facts"]
}}

pricing_transparency scores only how clearly the supplied text discloses pricing: published
prices or tiers, a free tier, usage rates, or an explicit rate card. Use null when the
supplied text contains no pricing information at all; null withholds the score and is not a
penalty. Never guess prices from prior knowledge.

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

implementable means the documentation is complete enough to build a working integration THAT DOES
THIS OBJECTIVE, assuming its own documented credentials are supplied. Two separate tests, and both
must pass:

1. CAPABILITY. {capability_rule}
   Do not infer a capability from the tool's category, its popularity, or what similar tools do.
   Absent evidence is a fail, not a pass — say the documentation does not show it.
2. INTEGRABILITY. The documentation is complete enough to actually build against.

Set implementable true only when BOTH hold. When it is false, say which test failed and what is
missing. Judge capability against the objective even when the constraints list is empty: an
absent constraints entry means nobody wrote the requirement down twice, not that it went away.

When execution_mode is "sandbox_verifiable", verification_code must be non-destructive, must not
invent endpoints, and must finish by printing PROOFBENCH_OK. It may validate SDK imports, client
construction, or documented unauthenticated behavior. Do not print or embed secrets. Keep install
commands minimal.

DOCUMENTATION:
{docs_text[:24000]}
{f'''
PRICING PAGE:
{pricing_text[:6000]}''' if pricing_text else ""}
"""


def _assessment_request(
    tool_name: str,
    docs_text: str,
    objective: str,
    available_credentials: list[str],
    constraints_text: str = "",
    pricing_text: str = "",
    candidate_role: str = "product",
    note: str = "",
) -> dict[str, Any]:
    """Build one assessment request.

    ``note`` is appended verbatim to the user message. It exists for the
    self-check repair pass, which quotes a detected contradiction back at the
    model; it uses the same shape as the in-loop validation retry below so there
    is one way to correct an assessment rather than two.
    """
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
                    constraints_text,
                    pricing_text,
                    candidate_role,
                ) + str(note or ""),
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
    constraints=None,
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

    ``constraints`` is the user's own stated environment, threaded through so
    feasibility is judged against the stack they actually run rather than in the
    abstract, where every well-documented product looks equally integrable.
    """
    from engine.llm_clients import capability_providers, provider_chat_completions

    runtime_env = dict(env or {})
    available_credentials = sorted(str(name) for name in entitled_credentials)
    constraints_text = format_constraints(constraints)
    requests = [
        _assessment_request(
            item["name"],
            item["docs_text"],
            objective,
            available_credentials,
            constraints_text,
            item.get("pricing_text", ""),
            str(item.get("role") or "product"),
            str(item.get("note") or ""),
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

    # The candidate, not the batch, is the unit of retry. The previous shape
    # returned as soon as ANY candidate in a batch parsed, which froze every
    # other candidate's one malformed reply into a permanent "Assessment
    # unavailable" row — a real eight-candidate run shipped three of them, a 37%
    # failure rate caused entirely by single unretried samples. Now each pass
    # re-requests only what is still unresolved, a validation failure is fed
    # back so the retry can correct rather than repeat, and a candidate is only
    # reported failed after every provider has had every pass at it.
    request_by_name = {item["name"]: request
                       for item, request in zip(candidates, requests)}
    plans: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    pending = list(candidates)
    last_error: Exception | None = None
    provider_timeout = _provider_timeout_seconds(runtime_env)
    for _sweep in range(MAX_ASSESSMENT_SWEEPS):
        for provider in providers:
            if not pending:
                break
            batch = []
            for item in pending:
                request = json.loads(json.dumps(request_by_name[item["name"]]))
                prior = errors.get(item["name"])
                if prior:
                    request["messages"][-1]["content"] += (
                        "\n\nIMPORTANT: a previous attempt at this assessment failed "
                        f"validation ({prior}). Return ONLY the JSON object, with every "
                        "required key present and every integer within its documented range."
                    )
                batch.append(request)
            try:
                responses = asyncio.run(
                    asyncio.wait_for(
                        provider_chat_completions(provider, batch, env=runtime_env),
                        timeout=provider_timeout,
                    )
                )
            except Exception as exc:
                last_error = exc
                continue
            for name, result in _collect(pending, responses).items():
                if "plan" in result:
                    plans[name] = result
                    errors.pop(name, None)
                else:
                    errors[name] = result["error"]
            pending = [item for item in pending if item["name"] not in plans]
        if not pending:
            break
    if plans or errors:
        return {**plans, **{name: {"error": message} for name, message in errors.items()}}
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
    constraints=None,
) -> dict[str, Any]:
    """Assess one tool through the same Doubleword batch path a run uses."""
    result = assess_documentation_batch(
        [{"name": tool_name, "docs_text": docs_text}],
        objective,
        env=env,
        entitled_credentials=entitled_credentials,
        constraints=constraints,
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
    # Nothing scored on any axis means the documentation could not be read at
    # all, typically a stale or 404 URL. That is a fact about the link, not
    # about the vendor, so it is withheld rather than published as 0/100.
    if not any((plan["documentation_quality"], plan["integration_feasibility"],
                plan["auth_clarity"])):
        return unavailable_result(plan["reason"])
    # The weights shift only when pricing evidence actually exists, so a
    # documentation set that publishes no prices is never penalised for it: it
    # is simply rated on the three axes that were measured.
    pricing = plan.get("pricing_transparency")
    if pricing is None:
        base = round(
            plan["documentation_quality"] * 0.30
            + plan["integration_feasibility"] * 0.50
            + plan["auth_clarity"] * 0.20
        )
    else:
        base = round(
            plan["documentation_quality"] * 0.25
            + plan["integration_feasibility"] * 0.45
            + plan["auth_clarity"] * 0.15
            + pricing * 0.15
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
        "pricing_transparency": pricing,
        "pricing_notes": plan.get("pricing_notes", ""),
        "setup_complexity": plan["setup_complexity"],
        "reason": plan["reason"],
        "evidence": plan["evidence"],
        # Only ever the commands the documentation itself supports: validation
        # empties this for a comparison-only or non-implementable plan, so a
        # build path can never print setup steps nothing documented.
        "build_commands": plan.get("build_commands") or [],
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
        "pricing_transparency": None,
        "pricing_notes": "",
        "setup_complexity": None,
        "reason": str(reason)[:600],
        "evidence": [],
        "build_commands": [],
    }


_BASIS_LABELS = {
    "sandbox_execution": "Executed",
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


def meets_requirements(values: dict) -> bool:
    """Whether this candidate can actually do the job the objective asked for.

    Only an explicit False is a failure. Extraction rows and older assessments
    carry no flag, and an absent flag is not a demotion.
    """
    return (values or {}).get("implementable") is not False


def rank_key(values: dict) -> tuple[int, int, int]:
    """Assessed at all, then requirement fit, then score.

    Score alone put a tool that cannot do the job above one that can: failing
    the requirement caps a rating at 49, which still beats a capable tool with
    thinner documentation scoring 43. Nothing else matters if the requirement is
    unmet, so it partitions the field rather than contributing to a total.

    Being assessed at all comes first because a withheld score is not a pass: a
    candidate whose documentation could never be read is unknown, and unknown
    must not outrank a tool that was measured and found wanting.
    """
    rating = values.get("rating")
    return (1 if rating is not None else 0,
            1 if meets_requirements(values) else 0,
            _score(values))


def _display(name: str, values: dict) -> str:
    """The vendor's own name for itself; the slug only when nothing else exists.

    A report about "Azure AI Search + Azure OpenAI" that calls it
    `azure_ai_search_openai` throughout reads like a database dump.
    """
    return str((values or {}).get("display_name") or name)


def build_path_is_the_answer(metrics: dict) -> list[tuple[str, dict]]:
    """The viable components, when every marketed product failed the requirement.

    Returns nothing whenever a product still works: an implementation plan is
    the answer to "nothing you can buy does this", and offering one beside a
    product that does would be advice the evidence does not support.
    """
    rows = [(name, values) for name, values in (metrics or {}).items()
            if isinstance(values, dict) and values.get("rating") is not None]
    products = [(n, v) for n, v in rows if v.get("role") != "build_component"]
    components = [(n, v) for n, v in rows if v.get("role") == "build_component"]
    viable = [(n, v) for n, v in components if v.get("implementable") is True]
    if not products or not viable:
        return []
    if any(v.get("implementable") is not False for _, v in products):
        return []
    return viable


def _unplanned_build_path(metrics: dict) -> list[str]:
    """The parts alone, only when the run concluded "build it" and lost the plan.

    This is the single place a separated per-component list may still appear.
    The healthy report presents one unified design with the components inside
    it; but a run that established nothing on the market does the job and then
    failed to generate a plan still owes the reader what it assessed, said
    plainly, rather than a verdict with nothing behind it.
    """
    components = sorted(build_path_is_the_answer(metrics), key=lambda item: -_score(item[1]))
    if not components:
        return []
    lines = [
        "",
        "## How to build this yourself",
        "",
        "No marketed product met the requirement. A full implementation plan could not "
        "be generated on this run, so what follows is the assessed components a "
        "self-built solution would be made of, without the design that joins them.",
        "",
    ]
    for name, values in components:
        line = f"- **{_display(name, values)}** — {values.get('rating')}/100"
        build_commands = values.get("build_commands") or []
        if build_commands:
            line += f"; documented setup: `{'; '.join(build_commands)}`"
        lines.append(line)
    lines.append("")
    return lines


def write_assessment_report(
    metrics: dict,
    citations: list[dict],
    out_path: str,
    excluded: list[dict] | None = None,
    build_plan: dict | None = None,
    self_check: list[dict] | None = None,
) -> str:
    """Write an evidence-led implementation feasibility report.

    ``excluded`` carries the candidates that left the field before any assessment
    ran. They are listed, never scored: nothing about them was measured, so they
    have no number. Two kinds share the section — a stated constraint ruled the
    candidate out, or discovery surfaced it and intake did not shortlist it. The
    second is a choice about attention rather than a strike, so it reads as one
    compact line and never borrows the shape of a finding.

    ``self_check`` carries the consistency flags that survived a re-assessment.
    They are printed after the findings, as caveats on rows that are still
    published: a row whose reason argues with its own number is worth reading,
    and worth reading with the argument attached.
    """
    # Only products are ranked. A build component is a PART of a self-built
    # solution, not a rival product: ranked together, a plotting library took
    # first place on a question about generating math questions, scoring 100 for
    # documentation quality it genuinely has and a capability it does not.
    # Requirement fit orders what remains — a product that cannot do the job
    # never outranks one that can, however much better its documentation is.
    products = {name: values for name, values in (metrics or {}).items()
                if (values or {}).get("role") != "build_component"}
    ranked = sorted(products.items(), key=lambda item: rank_key(item[1]), reverse=True)
    lines = [
        "# ProofBench Tool Implementation Report",
        "",
        "Suitability is scored from documentation evidence. The basis column states",
        "whether a candidate was executed in an isolated sandbox or compared from",
        "documentation only. Comparison-only products were never executed.",
        "",
        "## Ranked assessment",
        "",
        "| Rank | Tool | Suitability | Basis | Implementable | Verification | Docs | Feasibility | Auth | Pricing | Setup |",
        "|---:|---|---:|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for rank, (name, values) in enumerate(ranked, 1):
        rating = values.get("rating")
        implementable = values.get("implementable")
        basis = _BASIS_LABELS.get(values.get("assessment_basis"), "Documentation")
        lines.append(
            f"| {rank} | {_display(name, values)} | "
            f"{'n/a' if rating is None else f'{rating}/100'} | {basis} | "
            f"{'n/a' if implementable is None else ('Yes' if implementable else 'No')} | "
            f"{values.get('verification_status', 'unknown')} | "
            f"{_cell(values.get('documentation_quality'))} | "
            f"{_cell(values.get('integration_feasibility'))} | "
            f"{_cell(values.get('auth_clarity'))} | "
            f"{_cell(values.get('pricing_transparency'))} | "
            f"{_cell(values.get('setup_complexity'))} |"
        )
    # One answer, not two. The report used to print a parts list and then a plan
    # over the same parts, which read as two competing recommendations and left
    # the reader to reconcile them. The plan carries each component's rating and
    # documented setup inline, so the design IS the build path.
    from engine.build_plan import render_markdown

    lines.extend(render_markdown(build_plan) or _unplanned_build_path(metrics))
    lines.extend(["", "## Findings", ""])
    for name, values in ranked:
        if values.get("execution_mode") == "comparison_only":
            note = ("Compared from documentation evidence. This product was not executed, "
                    "so no runtime behaviour is claimed.")
        elif values.get("assessment_basis") == "sandbox_execution":
            note = (f"Executed in an isolated sandbox; verification "
                    f"{values.get('verification_status', 'unknown')}.")
        else:
            note = "Assessed from documentation evidence."
        lines.extend(
            [
                f"### {_display(name, values)}",
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
        pricing_notes = str(values.get("pricing_notes") or "").strip()
        if pricing_notes:
            lines.extend([f"Pricing: {pricing_notes}", ""])
    if self_check:
        # Only surviving flags reach here, and only when there are any. Silence
        # is the healthy state: a heading announcing that nothing was wrong
        # would teach readers to skip the one section that only appears when
        # something is.
        lines.extend([
            "## Self-check",
            "",
            "These rows were flagged by the automated consistency review and "
            "re-assessed once; the flags below survived. Read them as caveats on "
            "the rows above.",
            "",
        ])
        for flag in self_check:
            name = str(flag.get("name") or "")
            values = (metrics or {}).get(name) or {}
            lines.append(f"- **{_display(name, values)}** — {flag.get('detail', '')}")
        lines.append("")
    if excluded:
        # Stated as elimination, not as failure: these never reached an
        # assessment, so the section carries no score of any kind.
        violations = [i for i in excluded if str(i.get("kind") or "") != "not_assessed"]
        not_assessed = [i for i in excluded if str(i.get("kind") or "") == "not_assessed"]
        lines.extend(["## Considered and excluded", ""])
        if violations:
            lines.extend([
                "These candidates were dropped before assessment because a stated "
                "constraint ruled them out. They were not scored.",
                "",
            ])
            for item in violations:
                label = str(item.get("display_name") or item.get("name") or "Candidate")
                lines.append(f"- **{label}**: {item.get('violates', '')}")
            lines.append("")
        if not_assessed:
            lines.extend([
                "Also surfaced during discovery and not shortlisted. No requirement "
                "strike is recorded against these, and none of them was measured.",
                "",
            ])
            for item in not_assessed:
                label = str(item.get("display_name") or item.get("name") or "Candidate")
                lines.append(f"- {label} — not assessed")
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
