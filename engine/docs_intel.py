"""Documentation discovery and scraping through the Oxylabs realtime API."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests


OXYLABS_ENDPOINT = "https://realtime.oxylabs.io/v1/queries"


def _credentials() -> tuple[str, str]:
    username = os.environ.get("OXYLABS_USERNAME")
    password = os.environ.get("OXYLABS_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "Oxylabs credentials are missing; set OXYLABS_USERNAME and OXYLABS_PASSWORD"
        )
    return username, password


def _query(payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        OXYLABS_ENDPOINT,
        json=payload,
        auth=_credentials(),
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Oxylabs request failed with HTTP {response.status_code}: {response.text}"
        )
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


def web_search(query: str, n: int = 5) -> list[dict]:
    """Search Google and return normalized organic results."""
    body = _query({"source": "google_search", "query": query, "parse": True})
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


def scrape_page(url: str) -> str:
    """Scrape a URL and return the response content as text."""
    content = _first_content(_query({"source": "universal", "url": url}))
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


def gather_tool_docs(candidates: list[dict], out_dir: str) -> dict:
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
        docs_path.write_text(scrape_page(docs_url) if docs_url else "", encoding="utf-8")
        result["docs"][name] = str(docs_path)

        pricing_url = str(candidate.get("pricing_url") or "").strip()
        if pricing_url:
            pricing_path = docs_dir / f"{filename}_pricing.md"
            pricing_path.write_text(scrape_page(pricing_url), encoding="utf-8")
            result["pricing"][name] = str(pricing_path)
        else:
            result["pricing"][name] = None
    return result
