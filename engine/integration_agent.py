"""Bounded support agent for ProofBench provider configuration.

It answers questions about what this deployment already implements, and designs
integrations for what it does not. Which of those a request needs is decided
first, from the deployment's own facts, so a question about shipped behaviour is
not answered by reading a vendor's marketing page.

The agent may research and generate a proposal, but it never writes application
source, installs dependencies, changes credentials, or activates a connector.
Those remain explicit operator actions outside this module.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from engine import docs_intel, scrapers
from engine.llm_clients import (
    capability_providers,
    chat_client,
    provider_model,
)

MAX_MESSAGE_CHARS = 4_000
MAX_SOURCE_CHARS = 12_000
MAX_SOURCES = 4
MAX_RESPONSE_CHARS = 20_000
MAX_HISTORY_CHARS = 16_000
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "PASSWORD", "SECRET")
# The name the operator's key gets stored under. Deliberately narrower than the
# server's accepted set: this path exists so someone who does not know the
# variable name can still paste a key, and a key is always an API_KEY. The
# server re-validates on the write, so this is a display contract, not the gate.
_CREDENTIAL_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}_API_KEY$")
# Providers that already ship here authenticate with names the pattern above
# cannot express. They mirror server.main.EXTRA_PROVIDER_ENV_NAMES: offering a
# name the credentials endpoint would reject is worse than offering none, and
# inventing SCRAPE_DO_API_KEY for a provider whose variable is actually
# SCRAPEDO_API_TOKEN is exactly that failure.
_CREDENTIAL_ENV_EXTRA = frozenset({
    "OXYLABS_USERNAME", "OXYLABS_PASSWORD", "SCRAPEDO_API_TOKEN",
    "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_UNLOCKER_ZONE",
})


def readiness(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return the exact prerequisites for the Settings integration agent."""
    settings = dict(env or {})
    llms = capability_providers("codegen", settings)
    search = scrapers.configured_providers(settings, "search")
    scrape = set(scrapers.configured_providers(settings, "scrape"))
    documentation_provider = next((name for name in search if name in scrape), None)
    missing = []
    if not llms:
        missing.append("default_llm")
    if documentation_provider is None:
        missing.append("web_scraper")
    return {
        "ready": not missing,
        "llm": {
            "configured": bool(llms),
            "provider": llms[0] if llms else None,
        },
        "scraper": {
            "configured": documentation_provider is not None,
            "provider": documentation_provider,
        },
        "missing": missing,
    }


def _deployment_facts(env: dict[str, str]) -> str:
    """What this deployment already ships, as text the agent reasons over.

    Without this the agent has no way to answer "is X implemented" and falls
    back to reading vendor documentation, which can only ever tell it what the
    VENDOR offers — never what ProofBench has. That is how an already-shipped
    scraper gets reported as missing and proposed from scratch.

    Names and configured/not-configured only. No value ever appears here.
    """
    from engine.adapter_gen import FALLBACK_MODULES
    from engine.llm_clients import PROVIDERS

    lines = ["LLM providers implemented in ProofBench:"]
    for name, spec in PROVIDERS.items():
        state = "configured" if str(env.get(spec.api_key_env) or "").strip() else "no credential"
        lines.append(f"- {name}: credential {spec.api_key_env}, model {spec.model_env} ({state})")

    configured_scrapers = set(scrapers.configured_providers(env, "search")) | set(
        scrapers.configured_providers(env, "scrape"))
    lines.append("")
    lines.append("Web search and scraping providers implemented in ProofBench:")
    for name in scrapers.DEFAULT_ORDER:
        meta = scrapers.META.get(name, {})
        label = scrapers.LABELS.get(name, name)
        state = "configured" if name in configured_scrapers else "no credential"
        creds = ", ".join(meta.get("credentials", ())) or "none needed"
        lines.append(f"- {name} ({label}): {meta.get('role', '')}, "
                     f"credentials {creds} ({state})")

    lines.append("")
    lines.append("Candidate tools with a built-in adapter: " + ", ".join(sorted(FALLBACK_MODULES)))
    return "\n".join(lines)


def _json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("integration agent returned no JSON object")
        text = text[start:end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("integration agent response must be an object")
    return value


def _safe_sources(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        url = str(row.get("url") or "").strip()
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or url in seen):
            continue
        seen.add(url)
        sources.append({
            "title": str(row.get("title") or parsed.hostname)[:200],
            "url": url,
        })
        if len(sources) >= MAX_SOURCES:
            break
    return sources


def _research(
    message: str,
    env: dict[str, str],
    on_progress: Any = None,
) -> tuple[list[dict[str, str]], str]:
    """Search and read documentation, reporting each step as it happens.

    `on_progress` is optional so the plain request/response path stays
    unchanged; a caller that streams passes a callback to narrate the work.
    A failing callback must never abort the research it is only describing.
    """
    def report(**event: Any) -> None:
        if on_progress is None:
            return
        try:
            on_progress(event)
        except Exception:
            pass

    query = message
    report(phase="search", query=query)
    rows = docs_intel.web_search(query, n=8, env=env)
    sources = _safe_sources(rows)
    report(phase="found", count=len(sources))
    excerpts = []
    for source in sources:
        report(phase="read", title=source["title"], url=source["url"])
        try:
            body = docs_intel.scrape_page(source["url"], env=env)
        except Exception:
            report(phase="read_failed", title=source["title"], url=source["url"])
            continue
        text = str(body or "").strip()
        if text:
            report(
                phase="read_done",
                title=source["title"],
                url=source["url"],
                chars=len(text),
            )
            excerpts.append(
                f"SOURCE: {source['title']}\nURL: {source['url']}\n{text[:MAX_SOURCE_CHARS]}"
            )
        else:
            report(phase="read_empty", title=source["title"], url=source["url"])
    return sources, "\n\n".join(excerpts)


def _redact_configured_secrets(text: str, env: dict[str, str]) -> str:
    safe = str(text)
    values = {
        str(value)
        for name, value in env.items()
        if any(marker in str(name).upper() for marker in _SECRET_ENV_MARKERS)
        and len(str(value)) >= 8
    }
    for value in sorted(values, key=len, reverse=True):
        safe = safe.replace(value, "[REDACTED]")
    return safe


def _credential(value: Any) -> dict[str, str] | None:
    """Name the variable an operator's key should be saved under, or nothing.

    Only the NAME travels: the agent is never shown a key and never asks for one
    in prose. The client collects the value and writes it through the ordinary
    provider-keys endpoint, so a bad guess here can produce a useless variable
    name but can never leak or misroute a secret.
    """
    if not isinstance(value, dict):
        return None
    env = str(value.get("env") or "").strip().upper()
    if not _CREDENTIAL_ENV_RE.fullmatch(env) and env not in _CREDENTIAL_ENV_EXTRA:
        return None
    label = str(value.get("label") or "").strip()[:60]
    return {"env": env, "label": label or env}


def _plan(
    request: str,
    facts: str,
    conversation: str,
    provider: str,
    settings: dict[str, str],
) -> dict[str, Any]:
    """Decide what this request actually needs before doing any of it.

    Reading vendor documentation is the expensive path and it answers exactly
    one kind of question: what a vendor offers. "Is X implemented", "which
    scraper is active", "what does this setting do" are answered from what this
    deployment already knows, and researching them wastes a search, four page
    reads, and the operator's patience on a worse answer.
    """
    prompt = f"""You support an operator configuring ProofBench. Decide how to handle
one request. ProofBench integrates LLM providers, web search and scraping
providers, and candidate tools — the request may be about any of them, or about
the deployment itself.

What this deployment already implements:
{facts}

Prior conversation:
{conversation or "No prior turns."}

Request:
{request}

Return one strict JSON object with:
{{
  "thought": "One short sentence naming what is being asked and how you will answer it.",
  "action": "answer" | "research",
  "query": "A web search query. Only when action is research.",
  "answer": "The full answer in markdown. Only when action is answer."
}}

Choose "answer" when the facts above already settle it — anything about what
ProofBench implements, what is configured, which provider serves a role, or
what a setting means. Say plainly that something IS implemented when it is
listed above, and name its credential variable exactly as written.

Choose "research" only when the answer depends on what an external vendor
offers and the facts above do not cover it — adding a provider that is not
listed, or a vendor's endpoints, models, or authentication.
"""
    client = chat_client(provider, settings)
    response = client.chat.completions.create(
        model=provider_model(provider, settings),
        messages=[
            {
                "role": "system",
                "content": (
                    "You triage ProofBench configuration requests. You answer from the "
                    "deployment facts you are given whenever they suffice, and reach for "
                    "external documentation only when they do not. Return strict JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    value = _json_object(response.choices[0].message.content or "")
    action = str(value.get("action") or "").strip().lower()
    if action not in {"answer", "research"}:
        raise ValueError("integration agent returned an invalid plan")
    thought = str(value.get("thought") or "").strip()[:300]
    answer = str(value.get("answer") or "").strip()
    query = str(value.get("query") or "").strip()[:MAX_MESSAGE_CHARS]
    # A plan to answer with nothing to say is not an answer; fall through to
    # research rather than returning an empty turn.
    if action == "answer" and not answer:
        action = "research"
    if action == "research" and not query:
        query = f"{request} official API documentation"
    return {"action": action, "thought": thought, "answer": answer, "query": query}


def _bounded_history(history: list[dict[str, str]] | None) -> str:
    remaining = MAX_HISTORY_CHARS
    lines = []
    for item in (history or [])[-12:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content or remaining <= 0:
            continue
        content = content[:min(MAX_MESSAGE_CHARS, remaining)]
        lines.append(f"{role.upper()}: {content}")
        remaining -= len(content)
    return "\n\n".join(lines)


MAX_SUGGESTIONS = 8
_SUGGESTIBLE_ENV_RE = re.compile(r"^([A-Z][A-Z0-9_]{0,95})_(MODEL|BASE_URL)$")


def _suggestion(value: Any, kind: str) -> dict[str, str] | None:
    """One candidate value, or nothing if it is not the shape this setting takes."""
    if not isinstance(value, dict):
        return None
    text = str(value.get("value") or "").strip()
    if not text or len(text) > 200 or any(ch.isspace() for ch in text):
        return None
    if kind == "BASE_URL":
        parsed = urlsplit(text)
        # The server re-validates against its host allowlist on the write. This
        # is only about not offering something that is obviously not a URL.
        if parsed.scheme != "https" or not parsed.hostname:
            return None
    return {"value": text, "note": str(value.get("note") or "").strip()[:160]}


def suggest_values(env_name: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Research the values a non-secret provider setting can take.

    Model ids and base URLs are published facts an operator should not have to
    recall, so they are researched the same way a connector proposal is. Only
    settings that are NOT secrets are suggestible: there is nothing to look up
    about an API key, and asking a model to produce one would be nonsense.
    """
    name = str(env_name or "").strip().upper()
    match = _SUGGESTIBLE_ENV_RE.fullmatch(name)
    if not match:
        raise ValueError("only a provider MODEL or BASE_URL setting can be suggested")
    provider_name, kind = match.group(1), match.group(2)
    settings = dict(env or {})
    state = readiness(settings)
    if not state["ready"]:
        raise RuntimeError("integration agent prerequisites are not configured")

    # Only the leading token names the vendor. OPENAI_ORCHESTRATOR_MODEL would
    # otherwise be researched as "Openai Orchestrator", which is a ProofBench
    # role, not a company anyone has documentation for.
    subject = provider_name.split("_", 1)[0].title()
    topic = ("model ids" if kind == "MODEL" else "API base URL")
    sources, documentation = _research(f"{subject} API {topic}", settings)
    provider = str(state["llm"]["provider"])
    prompt = f"""An operator is filling in the ProofBench setting {name}.

Research excerpts:
{documentation or "No usable documentation page was retrieved."}

Return one strict JSON object with:
{{
  "summary": "One short sentence on how to choose between these.",
  "options": [
    {{"value": "The exact literal value to store, no quotes or prose.",
      "note": "A few words on when to pick it."}}
  ]
}}

List at most {MAX_SUGGESTIONS} real values, most generally useful first, taken
from the documentation you were given rather than invented. A value is a single
token with no spaces: a model id for a MODEL setting, an https URL for a
BASE_URL setting. Return an empty options list rather than guessing.
"""
    client = chat_client(provider, settings)
    response = client.chat.completions.create(
        model=provider_model(provider, settings),
        messages=[
            {
                "role": "system",
                "content": (
                    "You report the values a provider setting can take, from official "
                    "documentation. Return strict JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    value = _json_object(response.choices[0].message.content or "")
    raw = value.get("options")
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in (raw if isinstance(raw, list) else []):
        option = _suggestion(item, kind)
        if option and option["value"] not in seen:
            seen.add(option["value"])
            options.append(option)
        if len(options) >= MAX_SUGGESTIONS:
            break
    return {
        "env": name,
        "summary": str(value.get("summary") or "").strip()[:300],
        "options": options,
        "sources": sources,
    }


def respond(
    message: str,
    env: dict[str, str] | None = None,
    history: list[dict[str, str]] | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Answer one configuration request, researching only when that is what it needs."""
    request = str(message or "").strip()
    if not request:
        raise ValueError("message is required")
    if len(request) > MAX_MESSAGE_CHARS:
        raise ValueError(f"message must be at most {MAX_MESSAGE_CHARS} characters")
    settings = dict(env or {})
    state = readiness(settings)
    if not state["ready"]:
        raise RuntimeError("integration agent prerequisites are not configured")

    def report(**event: Any) -> None:
        if on_progress is None:
            return
        try:
            on_progress(event)
        except Exception:
            pass

    safe_request = _redact_configured_secrets(request, settings)
    provider = str(state["llm"]["provider"])
    conversation = _redact_configured_secrets(_bounded_history(history), settings)
    facts = _deployment_facts(settings)

    report(phase="thinking")
    plan = _plan(safe_request, facts, conversation, provider, settings)
    report(phase="plan", thought=plan["thought"], action=plan["action"])

    # A question this deployment can already answer is answered, not researched.
    if plan["action"] == "answer":
        return {
            "message": plan["answer"][:MAX_RESPONSE_CHARS],
            "sources": [],
            "implementation": {"status": "answer", "summary": plan["thought"] or "Answered."},
        }

    sources, documentation = _research(plan["query"], settings, on_progress)
    report(phase="compose", provider=provider)
    prompt = f"""The operator wants to add or adapt a service provider.

What this deployment already implements:
{facts}

Prior conversation:
{conversation or "No prior turns."}

Current request:

{safe_request}

Research excerpts:
{documentation or "No usable documentation page was retrieved."}

Return one strict JSON object with:
{{
  "message": "A concise technical response. Include any bounded connector or manifest code
              needed for the proposal, and clearly name unresolved inputs.",
  "implementation": {{
    "status": "proposal" | "needs_input" | "unsupported",
    "summary": "One short literal status summary."
  }},
  "credential": {{
    "env": "The exact environment variable name this provider's credential belongs in,
            for example MISTRAL_API_KEY. If the provider is already implemented above,
            use the variable named there verbatim; otherwise take it from the
            documentation you read. Never invent a spelling.",
    "label": "The provider's display name, for example Mistral."
  }}
}}

Include "credential" whenever the operator needs to supply a credential, so they
can paste it without knowing the variable name; they supply the value in the
interface and you never see or ask for it. Omit "credential" entirely when none
applies or no name is documented.

A provider listed above as implemented needs no connector: say it is already
implemented, name what it does, and offer its credential variable if it has no
credential yet.

Prefer an OpenAI-compatible configuration-only connector when the documentation
supports it. Otherwise propose the smallest custom HTTP connector. Never include
credentials or claim that code was installed, activated, executed, or validated
when the supplied documentation does not prove that. Do not suggest weakening
host allowlists, TLS checks, tenant scoping, redaction, sandbox credential
entitlements, or deterministic evaluation.
"""
    client = chat_client(provider, settings)
    response = client.chat.completions.create(
        model=provider_model(provider, settings),
        messages=[
            {
                "role": "system",
                "content": (
                    "You design bounded ProofBench provider integrations from official "
                    "documentation, for LLM providers, scraping providers, and candidate "
                    "tools alike. You produce proposals only. Return strict JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    value = _json_object(content or "")
    answer = str(value.get("message") or "").strip()
    implementation = value.get("implementation")
    if not answer or len(answer) > MAX_RESPONSE_CHARS:
        raise ValueError("integration agent returned an invalid message")
    if not isinstance(implementation, dict):
        raise ValueError("integration agent returned no implementation status")
    status = str(implementation.get("status") or "")
    summary = str(implementation.get("summary") or "").strip()
    if status not in {"proposal", "needs_input", "unsupported", "answer"}:
        raise ValueError("integration agent returned an invalid implementation status")
    if not summary or len(summary) > 500:
        raise ValueError("integration agent returned an invalid implementation summary")
    return {
        "message": answer,
        "sources": sources,
        "implementation": {"status": status, "summary": summary},
        # Absent rather than null when the agent named nothing usable, so the
        # client's check stays a plain truthiness test.
        **({"credential": credential}
           if (credential := _credential(value.get("credential"))) else {}),
    }
