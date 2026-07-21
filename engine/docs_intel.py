"""Documentation discovery and scraping through the Oxylabs realtime API.

When Oxylabs is unconfigured or fails, ``scrape_page`` falls back to a bounded
direct fetch (:func:`fetch_documentation`). That fallback is deliberately
narrower than Oxylabs: it reuses ``engine.network_security`` primitives, so the
target must be a public HTTPS host, every redirect hop is revalidated against
the same policy, process proxy variables are ignored, and private, loopback,
link-local, and cloud metadata addresses are refused. Response time, size,
redirect count, and content types are all bounded, and the caller receives
bounded readable text rather than an arbitrary byte stream.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urljoin

OXYLABS_ENDPOINT = "https://realtime.oxylabs.io/v1/queries"

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


def web_search(query: str, n: int = 5, env: dict[str, str] | None = None) -> list[dict]:
    """Search Google and return normalized organic results."""
    body = _query(
        {"source": "google_search", "query": query, "parse": True}, dict(env or {})
    )
    content = _first_content(body)
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Oxylabs search content was not valid JSON") from exc
    if not isinstance(content, dict):
        return []
    parsed_results = content.get("results")
    if not isinstance(parsed_results, dict):
        return []
    organic = parsed_results.get("organic")
    if not isinstance(organic, list):
        return []

    normalized = []
    for item in organic[: max(0, n)]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("snippet") or item.get("desc") or ""),
            }
        )
    return normalized


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

    The preferred path submits the target as data to Oxylabs and never fetches
    it from this process: our only direct connection is the fixed, allowlisted
    Oxylabs API, and Oxylabs resolves and fetches the submitted target. When
    Oxylabs is unconfigured or fails, we fall back to :func:`fetch_documentation`,
    which fetches the page directly under the bounds documented there.
    """
    from engine.network_security import validate_external_url

    safe_url = validate_external_url(url, require_https=False)
    try:
        content = _first_content(
            _query({"source": "universal", "url": safe_url}, dict(env or {}))
        )
    except Exception as exc:
        try:
            return fetch_documentation(safe_url)
        except Exception as fallback_exc:
            # Type names only: neither provider's error text is trusted output.
            raise RuntimeError(
                "documentation retrieval failed: "
                f"oxylabs {type(exc).__name__}, direct {type(fallback_exc).__name__}"
            ) from fallback_exc
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


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
        name = str(candidate.get("name") or "").strip()
        if not name:
            raise ValueError("each candidate must have a name")
        filename = _safe_filename(name)
        docs_url = str(candidate.get("docs_url") or "").strip()
        docs_path = docs_dir / f"{filename}.md"
        docs_path.write_text(
            scrape_page(docs_url, env=env) if docs_url else "", encoding="utf-8"
        )
        result["docs"][name] = str(docs_path)

        pricing_url = str(candidate.get("pricing_url") or "").strip()
        if pricing_url:
            pricing_path = docs_dir / f"{filename}_pricing.md"
            pricing_path.write_text(scrape_page(pricing_url, env=env), encoding="utf-8")
            result["pricing"][name] = str(pricing_path)
        else:
            result["pricing"][name] = None
    return result
