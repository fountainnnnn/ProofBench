"""Search through a self-hosted SearXNG instance.

The free fallback for the search half of intake: when no paid search provider
holds credentials, a local SearXNG instance answers instead, so a deployment
can still find candidate tools with zero commercial keys. It is search only —
SearXNG aggregates result links but does not fetch page bodies, which is
Crawl4AI's job in `engine.crawl4ai`.

Reachability, not a key, is what makes it "configured": a local HTTP service is
exactly what the hardened outbound policy forbids, so this provider is available
only when `PROOFBENCH_INSECURE_DEV=1` opens the local-service path. Point it
somewhere other than the default with `SEARXNG_BASE_URL`.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

DEFAULT_BASE_URL = "http://localhost:8080"
TIMEOUT_SECONDS = 30.0


def base_url(env: dict[str, str]) -> str:
    return str(env.get("SEARXNG_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL


def search_configured(env: dict[str, str]) -> bool:
    # A local service only answers when the local-HTTP path is open; claiming
    # otherwise would report readiness a run could not deliver.
    from engine import network_security

    return network_security.local_http_enabled()


# SearXNG aggregates links; it never returns page bodies, so it is not a scraper.
def scrape_configured(env: dict[str, str]) -> bool:
    return False


def reachable(env: dict[str, str] | None = None) -> bool:
    """Whether the local SearXNG instance is up right now.

    Reported so readiness reflects a service that can actually answer rather
    than one that is merely enabled. A refused connection means not reachable,
    never an exception to the caller.
    """
    from engine import network_security

    return network_security.local_service_listening(base_url(dict(env or {})))


def web_search(query: str, n: int = 10, env: dict[str, str] | None = None) -> list[dict]:
    """Query SearXNG's JSON API and return up to `n` normalized results."""
    from engine import network_security

    settings = dict(env or {})
    root, client = network_security.local_service_client(base_url(settings))
    try:
        response = client.get(
            urljoin(root + "/", "search"),
            params={"q": query, "format": "json"},
            timeout=TIMEOUT_SECONDS,
        )
    finally:
        client.close()
    if response.status_code != 200:
        raise RuntimeError(f"SearXNG request failed with HTTP {response.status_code}")
    try:
        parsed = json.loads(response.text)
    except (ValueError, json.JSONDecodeError) as exc:
        # A SearXNG instance with the JSON format disabled returns HTML here,
        # which is a configuration problem worth surfacing, not an empty result.
        raise RuntimeError("SearXNG did not return JSON; enable the json format") from exc
    rows = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return []

    normalized: list[dict] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append({
            "title": str(item.get("title") or ""),
            "url": url,
            "snippet": str(item.get("content") or ""),
        })
        if len(normalized) >= max(0, n):
            break
    return normalized
