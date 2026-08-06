"""Fetch a page through a self-hosted Crawl4AI service.

The free fallback for the scrape half of intake: when no paid provider holds
credentials, a local Crawl4AI server fetches and cleans documentation pages, so
a deployment can read a candidate's docs with zero commercial keys. It is scrape
only — finding the pages in the first place is SearXNG's job in `engine.searxng`.

Reachability, not a key, is what makes it "configured": a local HTTP service is
exactly what the hardened outbound policy forbids, so this provider is available
only when `PROOFBENCH_INSECURE_DEV=1` opens the local-service path. Point it
somewhere other than the default with `CRAWL4AI_BASE_URL`.

The target page is validated as a public URL before it reaches here, and it is
sent to Crawl4AI as request DATA — our own request goes only to the local
service, which then does the outward fetch itself.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

DEFAULT_BASE_URL = "http://localhost:11235"
TIMEOUT_SECONDS = 120.0


def base_url(env: dict[str, str]) -> str:
    return str(env.get("CRAWL4AI_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL


def _auth_headers(env: dict[str, str]) -> dict[str, str]:
    """Crawl4AI only listens beyond loopback when a token is set, so a container
    published to a host port always has one. Absent means an unauthenticated
    instance, which is equally valid."""
    token = str(env.get("CRAWL4AI_API_TOKEN") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def scrape_configured(env: dict[str, str]) -> bool:
    from engine import network_security

    return network_security.local_http_enabled()


# Crawl4AI fetches a known page; it does not run a search over the open web.
def search_configured(env: dict[str, str]) -> bool:
    return False


def reachable(env: dict[str, str] | None = None) -> bool:
    """Whether the local Crawl4AI service is up right now.

    Reported so readiness reflects a service that can actually answer rather
    than one that is merely enabled. A refused connection means not reachable,
    never an exception to the caller.
    """
    from engine import network_security

    return network_security.local_service_listening(base_url(dict(env or {})))


def _first_text(result: dict[str, Any]) -> str:
    """The cleanest body Crawl4AI offers, tolerating version differences.

    `markdown` is preferred and may be a string or an object carrying
    `raw_markdown`/`fit_markdown`; older builds only fill the HTML fields, which
    the caller cleans anyway.
    """
    markdown = result.get("markdown")
    if isinstance(markdown, str) and markdown.strip():
        return markdown
    if isinstance(markdown, dict):
        for key in ("fit_markdown", "raw_markdown"):
            value = markdown.get(key)
            if isinstance(value, str) and value.strip():
                return value
    for key in ("cleaned_html", "html", "extracted_content"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def scrape(url: str, env: dict[str, str] | None = None) -> str:
    """Fetch one page through Crawl4AI and return its cleaned content."""
    from engine import network_security

    settings = dict(env or {})
    root, client = network_security.local_service_client(base_url(settings))
    try:
        response = client.post(
            urljoin(root + "/", "crawl"),
            json={"urls": [url]},
            headers=_auth_headers(settings),
            timeout=TIMEOUT_SECONDS,
        )
    finally:
        client.close()
    if response.status_code != 200:
        raise RuntimeError(f"Crawl4AI request failed with HTTP {response.status_code}")
    try:
        parsed = json.loads(response.text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Crawl4AI did not return JSON") from exc

    # The server answers with a results list; a single-URL request still nests.
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if isinstance(results, list) and results and isinstance(results[0], dict):
        return _first_text(results[0])
    # Some versions return the single result object at the top level instead.
    if isinstance(parsed, dict):
        return _first_text(parsed)
    return ""
