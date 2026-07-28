"""Read the finished rows back before anyone else does.

The assessment pipeline validates every STEP — the plan schema, the score
ranges, the execution mode — and then never reads the FINISHED row again.
Nothing checks a reason against its own number, a row against the rule its own
prompt set, or a score against the evidence under it. Three escapes this week,
all caught by a human and not by the pipeline:

- a library rated 91 with implementable true whose reason never mentioned the
  objective's named requirement;
- components failed "for lacking X, which is a hard requirement", where X
  belongs to another part of the build entirely — the exact thing the component
  rule in the assessment prompt forbids;
- reason text asserting "meets all hard requirements" beside implementable
  false.

Every one of those is visible in the row itself. So this is a last pass over the
finished metrics: deterministic checks find self-contradictions, the
contradicting candidates are re-assessed ONCE with the contradiction quoted back
at the model, and whatever still contradicts itself is published as a caveat
rather than quietly shipped as a number.

Code never edits a rating or a verdict. It detects, it asks the model to look
again, and it reports what survived. A number this file rewrote would be a
number nothing measured.
"""
from __future__ import annotations

import re
from typing import Any

# Phrasings that assert a MISSING requirement. Deliberately narrow: a reason
# saying something negative is normal, and only an assertion that a required
# capability is ABSENT contradicts a passing verdict. Each entry is (regex,
# what it catches) and the verb lists are enumerated rather than left open so
# that "does not require an API key" — a compliment — can never match.
_NEGATIVE_REQUIREMENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "the documentation does not show/mention/support ..." — the prompt's own
    # wording for a capability fail. "require", "need" and "depend" are absent
    # from the verb list on purpose: they describe an absent BURDEN, not an
    # absent capability.
    (re.compile(
        r"\bdoes not\s+(?:appear to\s+)?"
        r"(?:show|mention|support|provide|document|include|cover|expose|offer|implement|generate|produce)\b",
        re.IGNORECASE), "the reason asserts a missing capability"),
    # "no evidence in the documentation that ..." — the prompt asks for exactly
    # this phrasing when a capability cannot be shown.
    (re.compile(r"\bno evidence\b", re.IGNORECASE),
     "the reason states no evidence was found"),
    # "lacks scheduled jobs" / "lacking webhook support" — a named absent
    # capability.
    (re.compile(r"\blacks\b|\blacking\b|\black of\b", re.IGNORECASE),
     "the reason names a lacking capability"),
    # "..., which is a hard requirement" — the row names the requirement it
    # just failed, which cannot sit under a passing verdict.
    (re.compile(r"\bwhich is a hard requirement\b", re.IGNORECASE),
     "the reason names a failed hard requirement"),
    # "does not meet the hard requirement" — the verdict spelled out in prose.
    (re.compile(r"\bdoes not\s+(?:meet|satisfy|fulfil|fulfill)\b", re.IGNORECASE),
     "the reason states a requirement is unmet"),
)

# Phrasings that assert the requirements ARE satisfied. Same discipline: a
# hedged "supports most of" is not a claim of satisfaction and must not match.
_POSITIVE_SATISFACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "meets all the hard requirements", "meets all stated requirements"
    (re.compile(r"\bmeets? all\b", re.IGNORECASE),
     "the reason claims all requirements are met"),
    # "meeting the hard requirements"
    (re.compile(r"\bmeeting the\s+(?:hard\s+)?requirements?\b", re.IGNORECASE),
     "the reason claims the requirements are met"),
    # "fulfils the objective's requirement"
    (re.compile(r"\bfulfil(?:s|ls)? the\b", re.IGNORECASE),
     "the reason claims the requirement is fulfilled"),
    # "supports all documented formats"
    (re.compile(r"\bsupports all\b", re.IGNORECASE),
     "the reason claims full support"),
    # "satisfies all / the / every hard requirement"
    (re.compile(r"\bsatisfies\s+(?:all|the|every)\b", re.IGNORECASE),
     "the reason claims the requirements are satisfied"),
)

# The component rule's own wording for a legitimate component failure: it
# contributes to NONE of the required capabilities. A component reason carrying
# this is following the rule, however negative the rest of it reads.
_COMPONENT_ACQUITTAL = re.compile(
    r"\bnone of\b|\bcontributes? to no\b|\bnot? any of the (?:objective|required)\b",
    re.IGNORECASE,
)

# result_from_plan caps a non-implementable candidate at 49. A row above that
# with implementable false did not come out of that function intact.
FAILURE_RATING_CAP = 49
# The score at which a reader stops reading and starts trusting. A row this high
# with nothing under it is a claim, not a measurement.
EVIDENCE_EXPECTED_FROM = 70

# Only these classes are self-contradictions the model can resolve by looking
# again. A corrupted score (code 4) and a missing evidence list (code 5) are not
# arguments with themselves, so re-asking would invent rather than correct.
REPAIRABLE_CODES = frozenset({
    "impl_true_reason_negative",
    "impl_false_reason_positive",
    "component_failed_specific",
})


def _excerpt(reason: str, match: re.Match[str]) -> str:
    """The sentence the contradiction lives in, so the flag can be checked."""
    start = reason.rfind(".", 0, match.start()) + 1
    end = reason.find(".", match.end())
    end = len(reason) if end == -1 else end + 1
    return reason[start:end].strip()[:200]


def _first_match(reason: str, patterns) -> tuple[str, str] | None:
    for pattern, description in patterns:
        match = pattern.search(reason)
        if match:
            return description, _excerpt(reason, match)
    return None


def find_contradictions(metrics: dict) -> list[dict]:
    """Flag finished rows that argue with themselves.

    Rows with no rating are skipped entirely. An unavailable row already says
    "nothing was measured" out loud; it has no number to contradict, and
    flagging it would turn the honest failure path into a source of noise.
    """
    flags: list[dict] = []
    for name, values in (metrics or {}).items():
        if not isinstance(values, dict):
            continue
        rating = values.get("rating")
        if rating is None:
            continue
        implementable = values.get("implementable")
        reason = str(values.get("reason") or "")
        role = values.get("role")

        if implementable is True:
            hit = _first_match(reason, _NEGATIVE_REQUIREMENT_PATTERNS)
            if hit:
                flags.append({
                    "name": name,
                    "code": "impl_true_reason_negative",
                    "detail": f"Rated implementable, but {hit[0]}: “{hit[1]}”",
                })

        if implementable is False:
            hit = _first_match(reason, _POSITIVE_SATISFACTION_PATTERNS)
            if hit:
                flags.append({
                    "name": name,
                    "code": "impl_false_reason_positive",
                    "detail": f"Rated not implementable, but {hit[0]}: “{hit[1]}”",
                })
            # The component rule: a part fails only when it contributes to NONE
            # of the objective's required capabilities. Naming one specific
            # missing capability fails it for work another part of the build was
            # always going to do.
            if role == "build_component" and not _COMPONENT_ACQUITTAL.search(reason):
                hit = _first_match(reason, _NEGATIVE_REQUIREMENT_PATTERNS)
                if hit:
                    flags.append({
                        "name": name,
                        "code": "component_failed_specific",
                        "detail": (
                            "This is one part of a build, but it was failed for one "
                            f"specific missing capability rather than for contributing to "
                            f"none of the required ones: “{hit[1]}”"
                        ),
                    })
            if isinstance(rating, int) and rating > FAILURE_RATING_CAP:
                flags.append({
                    "name": name,
                    "code": "score_above_failure_cap",
                    "detail": (
                        f"Not implementable but rated {rating}/100, above the "
                        f"{FAILURE_RATING_CAP} cap every failing row is scored under. "
                        "This row did not come out of the scoring path intact."
                    ),
                })

        if isinstance(rating, int) and rating >= EVIDENCE_EXPECTED_FROM and not (
            values.get("evidence") or []
        ):
            flags.append({
                "name": name,
                "code": "high_score_no_evidence",
                "detail": (
                    f"Rated {rating}/100 with no documented evidence listed, so the "
                    "score rests on the summary alone."
                ),
            })
    return flags


def _repair_note(details: list[str]) -> str:
    """Quote the contradiction back and say what a correct answer looks like."""
    return (
        "\n\nIMPORTANT: your previous assessment contradicted itself: "
        + " ".join(details)
        + ". Re-assess carefully; if the requirement evidence is genuinely absent, "
        "implementable must be false and the score capped accordingly; if present, "
        "the reason must cite it."
    )


def repair(
    metrics: dict,
    objective: str,
    flagged: list[dict],
    scraped_by_name: dict[str, dict],
    env: dict[str, str] | None = None,
    constraints: dict | None = None,
) -> dict:
    """Re-assess the self-contradicting candidates once, on their own documents.

    Returns ``{"metrics": <rows>, "repaired": [names]}``. The model, not this
    function, produces the new verdict: the contradiction is quoted into the
    same request the first assessment used and the reply goes through the same
    validation. A reply that does not validate changes nothing at all, because a
    row we could not re-derive is still a row a run genuinely measured.
    """
    from engine import tool_assessment

    updated = dict(metrics or {})
    notes: dict[str, list[str]] = {}
    for flag in flagged or []:
        if flag.get("code") in REPAIRABLE_CODES and flag.get("name") in scraped_by_name:
            notes.setdefault(flag["name"], []).append(str(flag.get("detail") or ""))
    if not notes:
        return {"metrics": updated, "repaired": []}

    candidates = []
    for name, details in notes.items():
        source = scraped_by_name[name]
        candidate = {
            "name": name,
            "docs_text": str(source.get("docs_text") or ""),
            "role": str(source.get("role") or "product"),
            "note": _repair_note(details),
        }
        pricing_text = str(source.get("pricing_text") or "")
        if pricing_text:
            candidate["pricing_text"] = pricing_text
        candidates.append(candidate)

    try:
        results = tool_assessment.assess_documentation_batch(
            candidates,
            objective,
            env=dict(env or {}),
            entitled_credentials=tool_assessment.ASSESSMENT_VERIFICATION_ENTITLEMENTS,
            constraints=constraints,
        )
    except Exception:
        # The re-assessment is a second opinion, never a gate. A provider outage
        # during it must leave the run exactly as it was.
        return {"metrics": updated, "repaired": []}

    repaired: list[str] = []
    for name in notes:
        result = (results or {}).get(name) or {}
        plan = result.get("plan")
        if not plan:
            continue
        original = updated.get(name) or {}
        try:
            row = tool_assessment.result_from_plan(
                plan,
                str(original.get("verification_status") or "not_applicable"),
                bool(original.get("daytona_triggered")),
            )
        except Exception:
            continue
        # A re-assessment that withholds every score is a worse row than the one
        # we already have, not a correction of it: the first pass measured
        # something and this one measured nothing.
        if row.get("rating") is None:
            continue
        # display_name and role are stamped on the row by the run, not by the
        # plan, so they are carried across rather than lost to the replacement.
        updated[name] = {**original, **row}
        repaired.append(name)
    return {"metrics": updated, "repaired": repaired}


def run_self_check(
    metrics: dict,
    objective: str,
    scraped_by_name: dict[str, dict] | None = None,
    env: dict[str, str] | None = None,
    constraints: dict | None = None,
) -> dict:
    """Check, repair once, report what survived.

    ``metrics`` is updated in place with any repaired rows so callers keep the
    dict they already hold. Returns ``{"flags": [...], "repaired": [names]}``;
    the flags are the ones that survived the second look and are published as
    caveats on the rows above them.
    """
    flags = find_contradictions(metrics)
    if not flags:
        return {"flags": [], "repaired": []}
    outcome = repair(
        metrics, objective, flags, dict(scraped_by_name or {}), env, constraints
    )
    if outcome["repaired"]:
        metrics.update(outcome["metrics"])
    return {"flags": find_contradictions(metrics), "repaired": outcome["repaired"]}
