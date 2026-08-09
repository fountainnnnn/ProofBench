"""How to build it yourself, when nothing on the market will.

A list of three libraries with their install commands is not an answer to "what
do I do now". It names parts and leaves the reader to work out the design: which
piece does what, what order they run in, where the output goes, and how any of
it reaches the system they already run.

So when the assessment concludes that self-implementation is the better path,
this turns the components it scored into an architecture — stack, the role each
component plays in THIS design, ordered steps, and how it plugs into the stated
environment.

Everything here is derived from documentation the run already read and scored.
The plan is a design over assessed parts, not new research, and it is generated
only when the scores already support building. It never becomes the reason to
build.
"""
from __future__ import annotations

import json
from typing import Any

MAX_STACK = 8
MAX_STEPS = 10
MAX_COMPONENTS = 6

BUILD_PLAN_SYSTEM = """You are a staff engineer writing the implementation plan a team will
follow to build a capability themselves, because no marketed product met their requirement.

You are given their objective, their stated environment, and the components that were
assessed from their own documentation. Design ONE coherent architecture using those
components. You may state that a component fills a role even if it is one of several
options, but do not introduce major new dependencies that were not assessed — if a piece
is genuinely missing from the set, say so in a step rather than inventing a product.
Do not claim that an unassessed service, existing platform, or configuration satisfies a
security, compliance, residency, budget, or reliability requirement. Name it as an
assumption or verification step instead. Only the supplied component evidence is measured.

Be concrete and specific to this objective. "Use a templating library" is useless; "render
the diagram to SVG with Matplotlib and inline it in the question payload" is the job.
Name the role each component plays in THIS design, not what it is in general.

Return strict JSON only, with exactly this shape and nothing else:
{
  "summary": "2-3 sentences: what gets built and how the pieces fit together",
  "stack": ["runtime, language, and any infrastructure the design assumes"],
  "components": [{"name": "<component name as given>", "role": "what it does in this design"}],
  "steps": ["ordered implementation steps, each one concrete enough to act on"],
  "integration": "how the result plugs into the environment they said they already run",
  "risks": ["what this design does not solve, or where it is likely to need work"]
}
Every list may be empty only if it genuinely has nothing to say."""


def _clean_list(value: Any, limit: int, size: int = 300) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:limit]:
        text = str(item or "").strip()[:size]
        if text:
            out.append(text)
    return out


def parse_plan(content: str, known_components: dict[str, dict]) -> tuple[dict | None, str | None]:
    """Validate a model-authored plan, or say tersely what was wrong with it.

    Nothing here is repaired. A plan with no summary and no steps says nothing a
    reader could act on, and printing an empty scaffold under a confident
    heading would be worse than printing no section at all. The reason travels
    back with the None so a retry can correct rather than repeat.

    ``known_components`` maps each assessed component's display name to the
    facts the run measured about it. Those facts are attached to the roles here
    rather than printed in a section of their own: the reader wants one design
    with the rating and the install line beside the part they belong to, not an
    architecture and then an inventory saying the same names again.
    """
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return None, "the reply was not valid JSON"
    if not isinstance(value, dict):
        return None, "the JSON was not an object"

    components = []
    for raw in (value.get("components") or [])[:MAX_COMPONENTS]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:160]
        role = str(raw.get("role") or "").strip()[:300]
        # A role for something that was never assessed is a recommendation the
        # run cannot stand behind, so it is dropped rather than printed.
        if name and role and (not known_components or name in known_components):
            facts = known_components.get(name) or {}
            components.append({
                "name": name,
                "role": role,
                "rating": facts.get("rating"),
                "documented_setup": list(facts.get("build_commands") or [])[:4],
            })

    plan = {
        "summary": str(value.get("summary") or "").strip()[:800],
        "stack": _clean_list(value.get("stack"), MAX_STACK, 120),
        "components": components,
        "steps": _clean_list(value.get("steps"), MAX_STEPS, 400),
        "integration": str(value.get("integration") or "").strip()[:600],
        "risks": _clean_list(value.get("risks"), 5, 300),
    }
    empty = [key for key in ("summary", "steps") if not plan[key]]
    if empty:
        return None, f"required key(s) missing or empty: {', '.join(empty)}"
    return plan, None


def _component_brief(name: str, values: dict) -> dict:
    """What the assessment already established about one component."""
    return {
        "name": str(values.get("display_name") or name),
        "rating": values.get("rating"),
        "documented_setup": (values.get("build_commands") or [])[:4],
        "evidence": (values.get("evidence") or [])[:5],
    }


def generate(objective: str, constraints: dict | None, components: list[tuple[str, dict]],
             *, env: dict[str, str] | None = None, complete=None,
             failure: dict | None = None) -> dict | None:
    """Design an implementation over the assessed components, or return nothing.

    Failure is always None rather than an exception: the plan is the most useful
    part of a no-product verdict, and also the most optional. A run that cannot
    produce one still reports the verdict, the ranking and the evidence.

    A malformed reply gets ONE repair round before that None: the reply and the
    reason it failed validation go back to the model so the retry can correct
    rather than repeat. ``failure``, when given, receives a ``detail`` string
    saying which kind of failure was final — the trace should be able to tell a
    dead provider from a model that could not produce the shape.
    """
    if failure is None:
        failure = {}
    if not components:
        return None
    if complete is None:
        from engine.agent import _orchestrator_complete

        complete = _orchestrator_complete
    request = json.dumps({
        "objective": objective or "",
        "environment": constraints or {},
        "assessed_components": [_component_brief(name, values) for name, values in components],
    }, indent=2)
    known = {str(v.get("display_name") or n): v for n, v in components}
    messages = [{"role": "system", "content": BUILD_PLAN_SYSTEM},
                {"role": "user", "content": request}]
    try:
        response = complete(env, messages=messages, temperature=0.2)
        reply = response.choices[0].message.content
    except Exception:
        failure["detail"] = "provider call failed"
        return None
    plan, reason = parse_plan(reply, known)
    if plan is not None:
        return plan
    # An empty reply has nothing worth showing back, and Anthropic-style
    # backends reject an assistant turn with empty content — so the retry
    # gets only the reason, not a replay that would fail every provider.
    replay = ([{"role": "assistant", "content": reply}]
              if isinstance(reply, str) and reply.strip() else [])
    try:
        response = complete(
            env,
            messages=messages + replay + [
                {"role": "user", "content":
                    f"IMPORTANT: that reply failed validation ({reason}). Return ONLY "
                    "the JSON object described in the system message, with every "
                    "required key present and non-empty."}],
            temperature=0.2,
        )
        plan, reason = parse_plan(response.choices[0].message.content, known)
    except Exception:
        failure["detail"] = "provider call failed"
        return None
    if plan is None:
        failure["detail"] = f"reply unparseable after repair ({reason})"
    return plan


def _part_line(item: dict) -> str:
    """One part of the design, carrying what the run measured about it.

    The score and the install line ride inside the plan rather than in a list of
    their own, so the reader never has to hold an architecture and an inventory
    side by side to learn what a part is and what it costs to pull in.
    """
    facts = []
    if item.get("rating") is not None:
        facts.append(f"{item['rating']}/100")
    if item.get("documented_setup"):
        facts.append("`" + "; ".join(item["documented_setup"]) + "`")
    detail = f" ({', '.join(facts)})" if facts else ""
    return f"- **{item['name']}**{detail} — {item['role']}"


def render_markdown(plan: dict | None) -> list[str]:
    """The plan as report markdown, or nothing at all."""
    if not plan:
        return []
    lines = ["", "## How to build this yourself", "", plan["summary"], ""]
    if plan["stack"]:
        lines.extend(["**Stack:** " + ", ".join(plan["stack"]), ""])
    if plan["components"]:
        lines.append("**What each part does**")
        lines.append("")
        lines.extend(_part_line(item) for item in plan["components"])
        lines.append("")
    if plan["steps"]:
        lines.append("**Implementation**")
        lines.append("")
        lines.extend(f"{index}. {step}" for index, step in enumerate(plan["steps"], 1))
        lines.append("")
    if plan["integration"]:
        lines.extend(["**Fitting it into your environment**", "", plan["integration"], ""])
    if plan["risks"]:
        lines.append("**What this does not solve**")
        lines.append("")
        lines.extend(f"- {risk}" for risk in plan["risks"])
        lines.append("")
    return lines
