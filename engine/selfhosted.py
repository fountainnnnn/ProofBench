"""The self-hosted, zero-key scraper: SearXNG to find pages, Crawl4AI to read them.

Neither half is a scraper on its own — SearXNG returns links but no page bodies,
Crawl4AI reads a known page but does not search — so they are presented as one
provider that covers both halves of intake, the way a paid provider does.

Availability here means *actually answering*, not merely enabled. A local service
that is not running would otherwise let readiness claim a capability a run cannot
deliver, so both halves are probed and the answer is cached briefly: the probe is
a health ping, and the cache keeps a per-request readiness check from turning into
two network round trips every time.
"""
from __future__ import annotations

import threading
import time

from engine import crawl4ai, searxng
from engine.crawl4ai import scrape
from engine.searxng import web_search

__all__ = ["search_configured", "scrape_configured", "web_search", "scrape",
           "reachable", "status", "reset_cache"]

# Long enough that a burst of readiness calls costs one probe, short enough that
# starting or stopping a container is reflected almost immediately.
CACHE_SECONDS = 5.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, bool]] = {}


def _probe(key: str, check, env) -> bool:
    """Probe with a short TTL. A failure is cached too, so a down service does
    not cost a fresh timeout on every call in the same burst."""
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
    alive = bool(check(env))
    with _lock:
        _cache[key] = (now, alive)
    return alive


def reset_cache() -> None:
    """Drop cached liveness. For tests and for an explicit re-check."""
    with _lock:
        _cache.clear()


def _searxng_up(env) -> bool:
    return _probe("searxng", searxng.reachable, env)


def _crawl4ai_up(env) -> bool:
    return _probe("crawl4ai", crawl4ai.reachable, env)


# Each capability needs only its own half to be up: SearXNG can serve search
# while Crawl4AI is down, and `docs_intel` falls back to its bounded direct fetch
# for the read. Reporting per half is more useful than an all-or-nothing answer.
def search_configured(env: dict[str, str]) -> bool:
    return _searxng_up(env)


def scrape_configured(env: dict[str, str]) -> bool:
    return _crawl4ai_up(env)


def reachable(env: dict[str, str] | None = None) -> bool:
    """True when both halves answer, which is what a complete intake needs."""
    settings = dict(env or {})
    return _searxng_up(settings) and _crawl4ai_up(settings)


def status(env: dict[str, str] | None = None) -> dict:
    """Per-half liveness, so the operator is told which service is down.

    "Not running" is not actionable when only one half is missing and the fix
    differs per service, so each half reports its own URL and state. The two
    probes run concurrently: a refused connection still costs a small timeout per
    resolved address, and this sits in the Settings request path.
    """
    from concurrent.futures import ThreadPoolExecutor

    from engine import network_security

    settings = dict(env or {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        searx = pool.submit(_searxng_up, settings)
        crawl = pool.submit(_crawl4ai_up, settings)
        searx_up, crawl_up = searx.result(), crawl.result()

    halves = [
        {"name": "SearXNG", "role": "search", "url": searxng.base_url(settings),
         "running": searx_up},
        {"name": "Crawl4AI", "role": "read", "url": crawl4ai.base_url(settings),
         "running": crawl_up},
    ]
    return {
        "enabled": network_security.local_http_enabled(),
        "running": all(half["running"] for half in halves),
        "services": halves,
    }
