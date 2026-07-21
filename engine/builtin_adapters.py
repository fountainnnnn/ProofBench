"""Server-owned registry of ProofBench's first-party benchmark adapters.

ProofBench executes real benchmarks only. Extraction candidates that ship with
the product are first-party source under ``engine.candidates.fallbacks``; a
model-generated adapter or a user-chosen candidate name never becomes one.

This module is the single authority on which environment variables each
built-in adapter may receive inside a Daytona sandbox. Entitlements are exact
names: there is no prefix or wildcard matching, and the mapping is keyed to
adapter source that ProofBench itself loads, not to a name supplied by a
client or an LLM. The server mints a one-use capability per built-in candidate
(``Orchestrator.register_trusted_candidate``) and binds credentials to that
capability, so a generated candidate that merely calls itself ``doubleword``
receives nothing.
"""

from __future__ import annotations

from engine.candidates.base import Candidate


class BuiltinAdapterUnavailable(RuntimeError):
    """A built-in adapter cannot run because required credentials are absent.

    Raised instead of silently degrading to a simulated or empty result.
    """

    def __init__(self, adapter: str, missing) -> None:
        self.adapter = str(adapter)
        self.missing = tuple(missing)
        super().__init__(
            f"built-in candidate {self.adapter!r} requires credentials that are not "
            f"configured: {', '.join(self.missing)}"
        )


# adapter name -> (required env names, optional env names)
#
# "Required" means the adapter source dereferences the variable unconditionally
# (``os.environ[...]``); without it the sandbox run raises KeyError. "Optional"
# means the source has a documented default (``os.environ.get(...)``) but should
# still receive the configured value when one exists.
BUILTIN_ADAPTER_CREDENTIALS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "tesseract": ((), ()),
    "easyocr": ((), ()),
    "paddleocr": ((), ()),
    "doubleword": (
        ("DOUBLEWORD_API_KEY", "DOUBLEWORD_MODEL"),
        ("DOUBLEWORD_BASE_URL",),
    ),
    "openai_vision": (("OPENAI_API_KEY",), ("OPENAI_VISION_MODEL",)),
    "nosana_vlm": (("NOSANA_BASE_URL", "NOSANA_API_KEY", "NOSANA_MODEL"), ()),
}

BUILTIN_ADAPTER_NAMES = frozenset(BUILTIN_ADAPTER_CREDENTIALS)

# Every exact name a first-party adapter may ever be entitled to. engine.agent
# consults this to narrow its orchestration-credential deny prefixes; no other
# credential may cross into a sandbox regardless of how a candidate is named.
SANDBOX_ELIGIBLE_CREDENTIALS = frozenset(
    name
    for required, optional in BUILTIN_ADAPTER_CREDENTIALS.values()
    for name in (*required, *optional)
)


def _canonical(name) -> str:
    return str(name or "").strip().casefold()


def is_builtin_adapter(name) -> bool:
    """True only for adapters whose source ProofBench ships and controls."""
    return _canonical(name) in BUILTIN_ADAPTER_CREDENTIALS


def required_credentials(name) -> tuple[str, ...]:
    return BUILTIN_ADAPTER_CREDENTIALS.get(_canonical(name), ((), ()))[0]


def optional_credentials(name) -> tuple[str, ...]:
    return BUILTIN_ADAPTER_CREDENTIALS.get(_canonical(name), ((), ()))[1]


def _configured(env, name: str) -> bool:
    return bool(str((env or {}).get(name) or "").strip())


def missing_credentials(name, env) -> tuple[str, ...]:
    """Required names this deployment has not configured, in declaration order."""
    return tuple(item for item in required_credentials(name) if not _configured(env, item))


def entitled_credentials(name, env) -> tuple[str, ...]:
    """Exact env names this built-in adapter may receive in a sandbox.

    Raises BuiltinAdapterUnavailable when a required credential is unconfigured,
    so the caller fails closed with an explicit preflight error.
    """
    if not is_builtin_adapter(name):
        raise KeyError(f"unknown built-in adapter: {name!r}")
    missing = missing_credentials(name, env)
    if missing:
        raise BuiltinAdapterUnavailable(_canonical(name), missing)
    return tuple(
        item
        for item in (*required_credentials(name), *optional_credentials(name))
        if _configured(env, item)
    )


def load_builtin_candidate(name) -> Candidate:
    """Load a first-party Candidate from ProofBench's own source tree."""
    from engine.adapter_gen import get_fallback

    if not is_builtin_adapter(name):
        raise KeyError(f"unknown built-in adapter: {name!r}")
    candidate = get_fallback(_canonical(name))
    if candidate is None:
        raise KeyError(f"built-in adapter source is unavailable: {name!r}")
    return candidate
