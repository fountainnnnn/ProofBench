"""Bounded support agent for ProofBench provider configuration.

It answers questions about what this deployment already implements, configures
what it can, and designs integrations for what it does not have. It works
through the tools in :mod:`engine.integration_tools`, deciding for itself which
to reach for: reading its own deployment state costs nothing and answers most
questions outright, so a question about shipped behaviour is not answered by
reading a vendor's marketing page.

The agent changes settings an operator can already change in the browser tab it
runs in — credentials, models, base URLs, the scraper chain — and every one of
those writes is delegated to the caller and re-validated there. It still never
writes application source, installs dependencies, or activates a connector, and
it never sees a credential value: those remain explicit operator actions, and
the boundary is enforced by which tools exist rather than by asking nicely.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from engine import docs_intel, integration_tools, scrapers
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

    # The chain order and the effective model are the two things an operator
    # asks about that names-and-configured-state alone cannot answer, and an
    # agent that can now change both has to be able to read both first.
    lines.append("")
    lines.append("Scraper chain order, most preferred first: "
                 + " ".join(scrapers.order_from_env(env)))
    lines.append("")
    lines.append("Effective model for each LLM provider:")
    for name, spec in PROVIDERS.items():
        model = str(env.get(spec.model_env) or "").strip()
        lines.append(f"- {name}: {model or spec.default_model}"
                     f"{'' if model else ' (default, not set here)'}")

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




MAX_TOOL_TURNS = 8
# One assistant turn per iteration plus its tool results. The cap exists so a
# model that loops on a failing tool ends the turn instead of the operator's
# patience, and it is generous enough that a real integration — check state,
# search, read two pages, write, confirm — finishes inside it comfortably.

SYSTEM_PROMPT = """You configure ProofBench for an operator, using tools.

You act rather than instruct. When the operator asks you to set something and
you have a tool for it, call the tool. Do not tell them to set an environment
variable, edit a file, or restart anything that you could have done yourself.

Read before you write. deployment_state tells you what ProofBench implements and
how it is configured right now; consult it before claiming anything is or is not
set up, and take variable names from it verbatim rather than inventing spellings.
Search and read vendor documentation only for facts about an external service.

You never see credentials. A key the operator pasted appears to you only as a
pasted_secret_N reference: pass that reference to save_credential. When a key is
needed and no reference exists, call request_credential to put a field in front
of them. Never ask for a key in prose and never repeat a reference into your
reply.

When you are done, reply in markdown. Say plainly what you changed, naming each
setting. Never claim something was installed, activated, executed, or verified
when no tool result shows it. Do not propose weakening host allowlists, TLS
checks, tenant scoping, redaction, sandbox credential entitlements, or
deterministic evaluation. You do not write application source, install
dependencies, or activate connectors — for those, propose the smallest bounded
change and leave it to the operator.
"""


def _tool_arguments(raw: Any) -> dict[str, Any]:
    """A tool call's arguments, or an empty mapping.

    Models occasionally emit no arguments at all for a no-argument tool, and
    occasionally emit malformed JSON under load. Neither is worth failing a
    turn over: the dispatcher validates every field it uses anyway, so an empty
    mapping produces an ordinary "that is required" result the agent can act on.
    """
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _assistant_turn(message: Any) -> dict[str, Any]:
    """One assistant message, in the shape the next request has to send back."""
    calls = list(getattr(message, "tool_calls", None) or [])
    turn: dict[str, Any] = {"role": "assistant", "content": getattr(message, "content", "") or ""}
    if calls:
        turn["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in calls
        ]
    return turn


def respond(
    message: str,
    env: dict[str, str] | None = None,
    history: list[dict[str, str]] | None = None,
    on_progress: Any = None,
    actions: integration_tools.Actions | None = None,
) -> dict[str, Any]:
    """Handle one configuration request, using tools until it is actually done.

    `actions` is what separates advice from work. Given one, the agent can store
    a credential, set a model or base URL, clear a setting, and reorder the
    scraper chain — the same writes the operator can already make in the tab
    this runs in, re-validated by the caller on the same path the HTTP endpoints
    use. Given none, the agent keeps every read tool and simply has nothing to
    write with, which is the correct behaviour for a caller that never opted in.
    """
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

    # Two passes, in this order. Redaction hides values this deployment already
    # holds; the vault lifts out the key the operator just pasted, which by
    # definition is not configured yet and so survives redaction untouched.
    vault = integration_tools.SecretVault()
    safe_request = vault.scrub(_redact_configured_secrets(request, settings))
    conversation = vault.scrub(_redact_configured_secrets(_bounded_history(history), settings))
    provider = str(state["llm"]["provider"])

    writable = actions is not None
    dispatcher = integration_tools.Dispatcher(
        env=settings,
        vault=vault,
        actions=actions or integration_tools.Actions(),
        facts=_deployment_facts,
        search=docs_intel.web_search,
        read=docs_intel.scrape_page,
        safe_sources=_safe_sources,
        credential=_credential,
        on_progress=report,
    )
    tools = integration_tools.definitions(writable)

    pasted = (
        "\n\nThe operator pasted "
        f"{'these secrets' if len(vault.references) > 1 else 'a secret'} in this "
        f"conversation, available to save_credential as: {', '.join(vault.references)}."
        if vault else ""
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Prior conversation:\n{conversation or 'No prior turns.'}\n\n"
            f"Request:\n{safe_request}{pasted}"
        )},
    ]

    client = chat_client(provider, settings)
    model = provider_model(provider, settings)
    answer = ""
    report(phase="thinking")
    for _ in range(MAX_TOOL_TURNS):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            temperature=0,
        )
        reply = response.choices[0].message
        calls = list(getattr(reply, "tool_calls", None) or [])
        if not calls:
            answer = str(getattr(reply, "content", "") or "").strip()
            break
        messages.append(_assistant_turn(reply))
        for call in calls:
            report(phase="tool", tool=call.function.name)
            result = dispatcher.run(call.function.name, _tool_arguments(call.function.arguments))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    else:
        # Out of turns with tools still pending. Ask once for the reply the
        # agent owes, without tools, so a long investigation ends in a summary
        # rather than in silence.
        report(phase="compose", provider=provider)
        messages.append({"role": "user", "content": (
            "Stop using tools and answer now: say what you did, what you found, "
            "and what is still outstanding.")})
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=0)
        answer = str(response.choices[0].message.content or "").strip()

    if not answer or len(answer) > MAX_RESPONSE_CHARS:
        raise ValueError("integration agent returned an invalid message")
    # A reference is a handle, not a secret, but it is also meaningless to a
    # person and reads like leaked plumbing. It should never appear; if a model
    # echoes one anyway, it does not reach the transcript.
    for reference in vault.references:
        answer = answer.replace(reference, "the key you pasted")

    changes = dispatcher.changes
    return {
        "message": answer,
        "sources": dispatcher.sources,
        "implementation": {
            "status": "applied" if changes else "answer",
            "summary": "; ".join(changes) if changes else "Answered.",
        },
        **({"credential": dispatcher.credential} if dispatcher.credential else {}),
    }
