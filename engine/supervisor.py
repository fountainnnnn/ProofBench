"""A distinct model corrects one concrete, deterministically-detected violation.

The user's rule is flat: the model that produced an artifact never reviews it.
Correlated bias and correlated laziness make same-model review worthless — a
model that missed a violation once will wave the same violation through a second
time, and one that cut a corner will defend the corner. So every correction here
is served by a DISTINCT ``(provider, model)`` identity resolved in
``engine.llm_clients.supervisor_identity``; when no distinct identity can be
guaranteed, this module refuses rather than quietly asking the primary again.

The contract is deliberately small and bounded:

- deterministic code, not a model, identifies the concrete violation and hands
  it here as text;
- the supervisor receives exactly the flawed (or absent) artifact, the exact
  violations, the output contract, and read-only context;
- exactly ONE toolless, temperature-0 correction call is made — no retries, no
  tools, no recursion into another supervisor;
- the reply is accepted only when it passes the SAME deterministic
  parser/normalizer/validator the primary path uses, supplied by the caller;
- the trace this returns is bounded and redacted by the caller's redactor, and a
  provider failure returns an outcome that changes nothing, so evidence a run
  already holds is never erased by a review that could not run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# The supervisor's raw reply is kept only to make the correction auditable. It is
# redacted by the caller and then clipped: a trace is a record, not a transcript.
MAX_TRACE_CHARS = 2_000
# Context and artifact are bounded before they reach the provider. A supervisor
# reasons over the violation and the contract, not over an unbounded page.
MAX_ARTIFACT_CHARS = 8_000
MAX_CONTEXT_CHARS = 12_000


@dataclass(frozen=True)
class SupervisionRequest:
    """Everything one correction call is allowed to see."""

    task: str
    contract: str
    violations: list[str]
    artifact: str = ""
    context: str = ""


@dataclass(frozen=True)
class SupervisionOutcome:
    """The result of a single supervision attempt.

    ``status`` is one of:
    - ``corrected``: the reply passed deterministic validation; ``parsed`` holds it.
    - ``no_supervisor``: no distinct identity is configured; nothing was called.
    - ``unavailable``: the provider call failed; evidence is untouched.
    - ``invalid``: a reply came back but failed deterministic validation.

    Only ``corrected`` carries a usable ``parsed`` value. Every other status
    leaves the caller holding exactly what it held before.
    """

    status: str
    detail: str
    identity: Any = None  # ModelIdentity | None, avoided as an import cycle risk
    parsed: Any = None
    raw: str = ""

    @property
    def corrected(self) -> bool:
        return self.status == "corrected"

    @property
    def independent(self) -> bool:
        """True only when a distinct model actually produced a usable correction."""
        return self.status == "corrected" and self.identity is not None


_SYSTEM = (
    "You are an independent supervisor. A different model produced (or failed to "
    "produce) an artifact and deterministic checks found concrete violations of a "
    "fixed output contract. Correct the artifact so it satisfies the contract and "
    "resolves every violation. Do not restate the violations, do not explain, do "
    "not ask questions: return only the corrected artifact in exactly the format "
    "the contract requires. If the contract asks for a fenced block, return that "
    "block and nothing else."
)


def _clip(value: object, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


def _build_user_message(request: SupervisionRequest) -> str:
    violations = "\n".join(f"- {v}" for v in request.violations) or "- (none stated)"
    parts = [
        f"TASK: {request.task}",
        "",
        "OUTPUT CONTRACT (the corrected artifact must satisfy this exactly):",
        request.contract.strip(),
        "",
        "CONCRETE VIOLATIONS the previous attempt must resolve:",
        violations,
    ]
    if request.artifact.strip():
        parts += [
            "",
            "PREVIOUS ATTEMPT (flawed — correct it, do not merely echo it):",
            _clip(request.artifact, MAX_ARTIFACT_CHARS),
        ]
    if request.context.strip():
        parts += [
            "",
            "READ-ONLY CONTEXT (conversation and gathered findings; do not invent "
            "beyond it):",
            _clip(request.context, MAX_CONTEXT_CHARS),
        ]
    return "\n".join(parts)


def supervise(
    request: SupervisionRequest,
    *,
    primary_capability: str,
    validate: Callable[[str], Any],
    env: dict | None = None,
    redact: Callable[[str], str] | None = None,
    exclude=None,
) -> SupervisionOutcome:
    """Run exactly one distinct-model correction attempt for ``request``.

    ``validate(raw_text)`` MUST be the caller's existing deterministic
    parser/normalizer/validator. It returns the parsed artifact on success or a
    falsey value (``None``/empty) on rejection; anything it raises is treated as a
    rejection. There is no second attempt: a supervisor that has to be nudged
    into a valid answer is not the independent check it was asked to be.

    ``exclude`` is the set of ``ModelIdentity`` values that ACTUALLY produced the
    artifact (after any failover, not merely the configured primary); the
    resolved supervisor is guaranteed to match none of them, so a fallback
    producer never ends up reviewing itself.
    """
    from engine.llm_clients import chat_client, supervisor_identity

    env = dict(env or {})
    redact = redact or (lambda value: str(value))
    identity = supervisor_identity(
        primary_capability, env, exclude=[i for i in (exclude or ()) if i is not None])
    if identity is None:
        return SupervisionOutcome(
            status="no_supervisor",
            detail=(
                "no distinct supervisor model is configured; set SUPERVISOR_PROVIDER "
                "or SUPERVISOR_MODEL to a model different from the primary producer"
            ),
        )

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _build_user_message(request)},
    ]
    try:
        # One toolless, temperature-0 call, on the hardened transport. No tools
        # are passed, so the supervisor cannot search, scrape, or recurse.
        response = chat_client(identity.provider, env).chat.completions.create(
            model=identity.model, messages=messages, temperature=0
        )
        raw = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 — a review must never break the run
        return SupervisionOutcome(
            status="unavailable",
            detail=f"{type(exc).__name__}: supervisor call failed; evidence unchanged",
            identity=identity,
        )

    safe_raw = _clip(redact(raw), MAX_TRACE_CHARS)
    try:
        parsed = validate(raw)
    except Exception:  # noqa: BLE001 — validation failure is a rejection, not a crash
        parsed = None
    if not parsed:
        return SupervisionOutcome(
            status="invalid",
            detail="supervisor output did not pass deterministic validation",
            identity=identity,
            raw=safe_raw,
        )
    return SupervisionOutcome(
        status="corrected",
        detail=f"corrected by {identity.label()}; passed deterministic validation",
        identity=identity,
        parsed=parsed,
        raw=safe_raw,
    )


def trace_artifact(request: SupervisionRequest, outcome: SupervisionOutcome) -> dict:
    """A bounded, secret-free trace record for the event stream.

    It names who reviewed (provider/model, never a credential) so an independent
    correction can be seen as independent, and it never claims independence that
    did not happen: a ``no_supervisor`` outcome says exactly that.
    """
    identity = outcome.identity
    summary = identity.label() if identity is not None else "no distinct supervisor"
    return {
        "kind": "trace",
        "tool": f"supervisor:{request.task}",
        "args_summary": summary,
        "status": "ok" if outcome.corrected else "error",
        "detail": outcome.detail,
    }
