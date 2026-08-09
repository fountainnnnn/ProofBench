"""Documentation discovery and scraping through an ordered provider chain.

Scrape.do, Oxylabs, and Bright Data are tried in the configured order. If every
configured provider fails, ``scrape_page`` falls back to a bounded direct fetch
(:func:`fetch_documentation`). That fallback reuses
``engine.network_security`` primitives, so the target must be a public HTTPS
host, every redirect hop is revalidated against the same policy, process proxy
variables are ignored, and private, loopback, link-local, and cloud metadata
addresses are refused. Response time, size, redirect count, and content types
are all bounded, and the caller receives bounded readable text rather than an
arbitrary byte stream.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urljoin

OXYLABS_ENDPOINT = "https://realtime.oxylabs.io/v1/queries"

# Concurrent page fetches. Scraping is pure network wait, but providers meter
# per account, so this stays low enough that a batch is served rather than
# throttled.
SCRAPE_CONCURRENCY = 4

# Sentinel: "caller did not override the resolver", distinct from an explicit
# None (which network_security reads as "skip DNS validation").
_UNSET = object()

# Bounds for the direct documentation fallback.
DIRECT_FETCH_MAX_REDIRECTS = 3
DIRECT_FETCH_TIMEOUT_SECONDS = 20.0
DIRECT_FETCH_MAX_BYTES = 512 * 1024
DIRECT_FETCH_MAX_TEXT_CHARS = 24000
DIRECT_FETCH_CONTENT_TYPES = frozenset({
    "text/html",
    "text/plain",
    "text/markdown",
    "application/xhtml+xml",
    "application/json",
})


def _credentials(env: dict[str, str]) -> tuple[str, str]:
    username = env.get("OXYLABS_USERNAME")
    password = env.get("OXYLABS_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "Oxylabs credentials are missing; set OXYLABS_USERNAME and OXYLABS_PASSWORD"
        )
    return username, password


def _query(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    from engine.network_security import secure_httpx_client

    endpoint, client = secure_httpx_client(
        OXYLABS_ENDPOINT, allowed_hosts={"realtime.oxylabs.io"}
    )
    try:
        response = client.post(
            endpoint,
            json=payload,
            auth=_credentials(env),
            timeout=60,
        )
    finally:
        client.close()
    if response.status_code != 200:
        raise RuntimeError(f"Oxylabs request failed with HTTP {response.status_code}")
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Oxylabs returned an invalid JSON response") from exc
    if not isinstance(body, dict):
        raise RuntimeError("Oxylabs returned an unexpected response shape")
    return body


def _first_content(body: dict[str, Any]) -> Any:
    results = body.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise RuntimeError("Oxylabs response did not contain results[0].content")
    if "content" not in results[0]:
        raise RuntimeError("Oxylabs response did not contain results[0].content")
    return results[0]["content"]


# Google serves ~10 organic results per page and, since it deprecated `num` in
# September 2025, ignores any request for more. Depth therefore means pages.
RESULTS_PER_PAGE = 10
MAX_SEARCH_PAGES = 4


def _organic_pages(body: dict[str, Any]) -> list[dict]:
    """Organic rows across every page in one Oxylabs response.

    A paged query answers with one entry in ``results`` per page, so reading
    ``results[0]`` alone silently discarded everything past the first ten.
    """
    pages = body.get("results")
    if not isinstance(pages, list):
        return []
    rows: list[dict] = []
    for page in pages:
        content = page.get("content") if isinstance(page, dict) else None
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Oxylabs search content was not valid JSON") from exc
        if not isinstance(content, dict):
            continue
        parsed = content.get("results")
        organic = parsed.get("organic") if isinstance(parsed, dict) else None
        if isinstance(organic, list):
            rows.extend(item for item in organic if isinstance(item, dict))
    return rows


def oxylabs_search(query: str, n: int = 5, env: dict[str, str] | None = None) -> list[dict]:
    """Search Google through Oxylabs, which returns every page in one request."""
    settings = dict(env or {})
    pages = max(1, min(MAX_SEARCH_PAGES, -(-max(1, n) // RESULTS_PER_PAGE)))
    body = _query(
        {"source": "google_search", "query": query, "parse": True,
         "start_page": 1, "pages": pages},
        settings,
    )
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in _organic_pages(body):
        url = str(item.get("url") or "").strip()
        # Paged results overlap at the seams; the same page twice is not depth.
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(
            {
                "title": str(item.get("title") or ""),
                "url": url,
                "snippet": str(item.get("snippet") or item.get("desc") or ""),
            }
        )
        if len(normalized) >= max(0, n):
            break
    return normalized


def web_search(query: str, n: int = 5, env: dict[str, str] | None = None) -> list[dict]:
    """Search Google through the configured providers, in their configured order.

    Asking for more than one page is what surfaces anything not already famous:
    the first page of Google is the popular answer by construction, so a tool at
    rank 15 is invisible to a single-page search.

    Every configured provider is tried. A search that returns nothing ends an
    intake turn with no candidates at all — the worst outcome this product has —
    so one more call is always cheaper than the alternative. An empty result is
    treated as a failure for the same reason: one provider answering 200 with no
    organic rows should not end the search while another is still available.
    """
    from engine import scrapers

    settings = dict(env or {})
    providers = scrapers.configured_providers(settings, "search")
    if not providers:
        # Loudly, not as an empty result: a deployment with no scraper
        # credentials is misconfigured, and "no tools found" would read as an
        # answer about the internet rather than about this install.
        raise RuntimeError("no search provider is configured")
    failure: Exception | None = None
    for name in providers:
        try:
            if name == "oxylabs":
                results = oxylabs_search(query, n=n, env=settings)
            else:
                results = scrapers._module(name).web_search(query, n=n, env=settings)
            if results:
                return results
        except Exception as exc:
            failure = exc
    if failure is not None:
        raise RuntimeError(
            "web search failed across every configured provider: " + ", ".join(providers)
        ) from failure
    # Every provider answered, none had anything. That is a real empty result.
    return []


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1\s*>")
_TAG_RE = re.compile(r"(?s)<[^>]*>")
_INLINE_SPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n\s*\n+")


def _content_type(header: str) -> tuple[str, str]:
    """Split a Content-Type header into (lowercased media type, charset)."""
    parts = str(header or "").split(";")
    media = parts[0].strip().casefold()
    charset = "utf-8"
    for parameter in parts[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().casefold() == "charset" and value.strip():
            charset = value.strip().strip('"').casefold()
    return media, charset


def _readable_text(body: str, media_type: str) -> str:
    """Reduce a fetched document to bounded, readable plain text."""
    if media_type in {"text/html", "application/xhtml+xml"}:
        body = _SCRIPT_STYLE_RE.sub(" ", body)
        body = _TAG_RE.sub(" ", body)
        body = unescape(body)
    body = _INLINE_SPACE_RE.sub(" ", body.replace("\r\n", "\n").replace("\r", "\n"))
    body = _BLANK_LINES_RE.sub("\n\n", body)
    return body.strip()[:DIRECT_FETCH_MAX_TEXT_CHARS]


def _read_bounded(response) -> bytes:
    payload = bytearray()
    for chunk in response.iter_bytes():
        payload.extend(chunk)
        if len(payload) >= DIRECT_FETCH_MAX_BYTES:
            return bytes(payload[:DIRECT_FETCH_MAX_BYTES])
    return bytes(payload)


def fetch_documentation(url: str, *, resolver: Any = _UNSET) -> str:
    """Fetch one public documentation page directly, within hard bounds.

    This is the fallback used when Oxylabs is unavailable. It is intentionally
    stricter than the Oxylabs path: the target and every redirect hop must pass
    ``validate_external_url`` (public HTTPS host, no credentials in the URL, no
    literal private/loopback/link-local/metadata address, and DNS answers
    re-checked per hop), the transport ignores process proxy variables, and
    redirects are followed manually so a cross-host hop gets a freshly pinned
    client rather than inheriting the previous hop's approval.

    ``resolver`` mirrors the seam ``engine.network_security`` already exposes and
    exists so tests can supply a controlled resolver instead of doing live DNS.
    Production callers leave it unset and get ``socket.getaddrinfo``.

    Returns bounded readable text. Raises RuntimeError on any bound violation.
    """
    from engine.network_security import secure_httpx_client, validate_external_url

    def _validate(candidate: str) -> str:
        if resolver is _UNSET:
            return validate_external_url(candidate)
        return validate_external_url(candidate, resolver=resolver)

    target = _validate(url)
    deadline = monotonic() + DIRECT_FETCH_TIMEOUT_SECONDS
    for _hop in range(DIRECT_FETCH_MAX_REDIRECTS + 1):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeError("direct documentation fetch exceeded its time budget")
        # A new client per hop: secure_httpx_client pins the transport to this
        # hop's hostname, so a redirect can never reuse the prior approval.
        safe_url, client = secure_httpx_client(target)
        try:
            with client.stream(
                "GET",
                safe_url,
                timeout=remaining,
                headers={"accept": "text/html,text/plain,text/markdown,application/json",
                         "user-agent": "ProofBench-docs/1.0"},
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location", "").strip()
                    if not location:
                        raise RuntimeError("redirect response did not supply a location")
                    target = _validate(urljoin(safe_url, location))
                    continue
                if response.status_code != 200:
                    raise RuntimeError(
                        f"direct documentation fetch failed with HTTP {response.status_code}"
                    )
                media_type, charset = _content_type(response.headers.get("content-type"))
                if media_type not in DIRECT_FETCH_CONTENT_TYPES:
                    raise RuntimeError("direct documentation fetch returned an unsupported content type")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > DIRECT_FETCH_MAX_BYTES:
                    raise RuntimeError("direct documentation fetch exceeded its size budget")
                payload = _read_bounded(response)
        finally:
            client.close()
        try:
            body = payload.decode(charset, errors="replace")
        except LookupError:
            body = payload.decode("utf-8", errors="replace")
        text = _readable_text(body, media_type)
        if not text:
            raise RuntimeError("direct documentation fetch returned no readable text")
        return text
    raise RuntimeError("direct documentation fetch exceeded its redirect budget")


def scrape_page(url: str, env: dict[str, str] | None = None) -> str:
    """Scrape a URL and return the response content as text.

    Configured providers are tried in the tenant's selected order. Each client
    connects only to its own fixed, allowlisted API host. If none returns
    content, :func:`fetch_documentation` performs the strictly bounded direct
    fetch documented above.
    """
    from engine import scrapers
    from engine.network_security import validate_external_url

    safe_url = validate_external_url(url, require_https=False)
    settings = dict(env or {})
    failure: Exception | None = None
    for name in scrapers.configured_providers(settings, "scrape"):
        try:
            if name == "oxylabs":
                content = oxylabs_scrape(safe_url, settings)
            else:
                content = scrapers._module(name).scrape(safe_url, settings)
        except Exception as exc:
            failure = exc
            continue
        if content is None:
            return ""
        if not isinstance(content, str):
            return json.dumps(content, ensure_ascii=False)
        # Providers return the raw document. Without this the model reads
        # stylesheets and inline scripts instead of the documentation, and rates
        # the vendor on what it could not find there.
        return _readable_text(content, "text/html")
    try:
        return fetch_documentation(safe_url)
    except Exception as fallback_exc:
        # Type names only: no provider's error text is trusted output.
        raise RuntimeError(
            "documentation retrieval failed: "
            f"providers {type(failure).__name__ if failure else 'unconfigured'}, "
            f"direct {type(fallback_exc).__name__}"
        ) from fallback_exc


def oxylabs_scrape(safe_url: str, settings: dict[str, str]):
    """Fetch one page through Oxylabs, rendered first.

    Current documentation sites build their content in the browser, and an
    unrendered fetch returns the page shell. Assessments read from that shell
    reported "no API endpoints documented" for vendors whose documentation is
    complete, so the rendered attempt leads and the plain one is the fallback.
    """
    attempts = ({"source": "universal", "url": safe_url, "render": "html"},
                {"source": "universal", "url": safe_url})
    failure: Exception | None = None
    for payload in attempts:
        try:
            return _first_content(_query(payload, settings))
        except Exception as exc:
            failure = exc
    raise failure if failure else RuntimeError("Oxylabs returned no content")


def _safe_filename(name: str) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not filename:
        raise ValueError("candidate name cannot produce an empty filename")
    return filename


def gather_tool_docs(
    candidates: list[dict], out_dir: str, env: dict[str, str] | None = None
) -> dict:
    """Scrape candidate documentation and pricing pages into markdown files."""
    docs_dir = Path(out_dir) / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict[str, str | None]] = {"docs": {}, "pricing": {}}

    for candidate in candidates:
        if not str(candidate.get("name") or "").strip():
            raise ValueError("each candidate must have a name")

    # One entry per page to fetch. Every candidate's docs and pricing pages are
    # independent network waits, so fetching them serially cost the sum of all
    # of them; the writes below still happen in candidate order.
    jobs = []
    for candidate in candidates:
        name = str(candidate.get("name") or "").strip()
        filename = _safe_filename(name)
        docs_url = str(candidate.get("docs_url") or "").strip()
        jobs.append((name, "docs", docs_url, docs_dir / f"{filename}.md"))
        pricing_url = str(candidate.get("pricing_url") or "").strip()
        if pricing_url:
            jobs.append(
                (name, "pricing", pricing_url, docs_dir / f"{filename}_pricing.md")
            )
        else:
            result["pricing"][name] = None

    def fetch(job):
        _, _, url, _ = job
        return scrape_page(url, env=env) if url else ""

    with ThreadPoolExecutor(max_workers=min(SCRAPE_CONCURRENCY, len(jobs) or 1)) as ex:
        bodies = list(ex.map(fetch, jobs))

    for (name, kind, _url, path), body in zip(jobs, bodies):
        path.write_text(body, encoding="utf-8")
        result[kind][name] = str(path)
    return result
