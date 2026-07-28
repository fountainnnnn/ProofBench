"""Search and scraping through Bright Data's unified request API.

Two zones, one endpoint: a SERP zone answers `web_search` and a Web Unlocker
zone answers `scrape_docs`. Both are reached by POSTing the target as data, so
this process never connects to the target itself — the same property the Oxylabs
path has, and the reason `secure_httpx_client` can lock the outbound host to a
single fixed API.

Two details here were found by measurement rather than read off the docs, and
both matter:

* **`num` is dead.** Google deprecated it in September 2025, so a query returns
  about ten organic results no matter what you ask for. Depth comes from
  paginating with `start`, not from requesting a bigger page.
* **`start` requires `brd_json=1`.** Without it the API answers "Error while
  processing request" for any page after the first, which reads like a broken
  zone rather than a missing parameter.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote_plus

ENDPOINT = "https://api.brightdata.com/request"
ALLOWED_HOSTS = frozenset({"api.brightdata.com"})

# Google serves ~10 organic results per page and ignores requests for more.
RESULTS_PER_PAGE = 10
# Depth is what surfaces niche tools, but each page is a billable request and an
# interactive chat turn is waiting, so pages are fetched concurrently and capped.
MAX_PAGES = 4
TIMEOUT_SECONDS = 90.0


def _configured(env: dict[str, str], zone_key: str) -> tuple[str, str] | None:
    token = str(env.get("BRIGHTDATA_API_TOKEN") or "").strip()
    zone = str(env.get(zone_key) or "").strip()
    return (token, zone) if token and zone else None


def search_configured(env: dict[str, str]) -> bool:
    return _configured(env, "BRIGHTDATA_SERP_ZONE") is not None


def scrape_configured(env: dict[str, str]) -> bool:
    return _configured(env, "BRIGHTDATA_UNLOCKER_ZONE") is not None


def _request(zone_key: str, url: str, env: dict[str, str]) -> str:
    from engine.network_security import secure_httpx_client

    credentials = _configured(env, zone_key)
    if not credentials:
        raise RuntimeError(f"Bright Data is not configured; set BRIGHTDATA_API_TOKEN and {zone_key}")
    token, zone = credentials
    endpoint, client = secure_httpx_client(ENDPOINT, allowed_hosts=set(ALLOWED_HOSTS))
    try:
        response = client.post(
            endpoint,
            json={"zone": zone, "url": url, "format": "raw"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SECONDS,
        )
    finally:
        client.close()
    if response.status_code != 200:
        raise RuntimeError(f"Bright Data request failed with HTTP {response.status_code}")
    return response.text


def _organic(body: str) -> list[dict[str, Any]]:
    """Parsed organic rows, or nothing.

    Bright Data reports an exhausted or unparseable page as a plain-text body
    rather than an error status, so a decode failure here means "no results on
    this page", not "the request failed".
    """
    try:
        parsed = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return []
    organic = parsed.get("organic") if isinstance(parsed, dict) else None
    return organic if isinstance(organic, list) else []


def _page_url(query: str, start: int) -> str:
    # brd_json=1 is not optional: without it any start>0 page errors out.
    return (f"https://www.google.com/search?q={quote_plus(query)}"
            f"&brd_json=1&start={start}")


def web_search(query: str, n: int = 10, env: dict[str, str] | None = None) -> list[dict]:
    """Search Google and return up to `n` normalized organic results."""
    settings = dict(env or {})
    pages = max(1, min(MAX_PAGES, -(-max(1, n) // RESULTS_PER_PAGE)))
    starts = [index * RESULTS_PER_PAGE for index in range(pages)]

    def fetch(start: int) -> list[dict[str, Any]]:
        try:
            return _organic(_request("BRIGHTDATA_SERP_ZONE", _page_url(query, start), settings))
        except Exception:
            # One page failing must not lose the pages that worked. The first
            # page is the exception: if it raises, the caller should see it.
            if start == 0:
                raise
            return []

    if pages == 1:
        collected = fetch(0)
    else:
        with ThreadPoolExecutor(max_workers=pages) as pool:
            collected = [row for page in pool.map(fetch, starts) for row in page]

    normalized: list[dict] = []
    seen: set[str] = set()
    for item in collected:
        if not isinstance(item, dict):
            continue
        # Bright Data names these link/description where Oxylabs says url/snippet;
        # callers see one shape regardless of which provider answered.
        url = str(item.get("link") or item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append({
            "title": str(item.get("title") or ""),
            "url": url,
            "snippet": str(item.get("description") or item.get("snippet") or ""),
        })
        if len(normalized) >= max(0, n):
            break
    return normalized


def scrape(url: str, env: dict[str, str] | None = None) -> str:
    """Fetch one page through Web Unlocker, which renders JavaScript itself."""
    return _request("BRIGHTDATA_UNLOCKER_ZONE", url, dict(env or {}))
