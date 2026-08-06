"""Bounded research assistant for designing new LLM provider integrations.

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

    query = f"{message} official API documentation"
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


def respond(
    message: str,
    env: dict[str, str] | None = None,
    history: list[dict[str, str]] | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Research one provider request and return a bounded, non-activating proposal."""
    request = str(message or "").strip()
    if not request:
        raise ValueError("message is required")
    if len(request) > MAX_MESSAGE_CHARS:
        raise ValueError(f"message must be at most {MAX_MESSAGE_CHARS} characters")
    settings = dict(env or {})
    state = readiness(settings)
    if not state["ready"]:
        raise RuntimeError("integration agent prerequisites are not configured")

    safe_request = _redact_configured_secrets(request, settings)
    sources, documentation = _research(safe_request, settings, on_progress)
    provider = str(state["llm"]["provider"])
    if on_progress is not None:
        try:
            on_progress({"phase": "compose", "provider": provider})
        except Exception:
            pass
    conversation = _redact_configured_secrets(_bounded_history(history), settings)
    prompt = f"""The operator wants to add or adapt an LLM service provider.

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
  }}
}}

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
                    "documentation. You produce proposals only. Return strict JSON."
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
    if status not in {"proposal", "needs_input", "unsupported"}:
        raise ValueError("integration agent returned an invalid implementation status")
    if not summary or len(summary) > 500:
        raise ValueError("integration agent returned an invalid implementation summary")
    return {
        "message": answer,
        "sources": sources,
        "implementation": {"status": status, "summary": summary},
    }
