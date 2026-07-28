"""A wrong logo is worse than no logo, and a stale one is worse than a slow one.

The build-time script could only bundle marks for tools that existed when the
frontend was built, so every new benchmark rendered blank monograms until
someone re-ran it and redeployed. These tests cover the runtime resolver that
replaced that chore, and the two rules that keep it honest: it only ever fetches
from a candidate's own stored docs URL, and it refuses a mark that came from
somewhere other than where it looked.
"""
from __future__ import annotations

import time

import pytest

from server import brand


# ------------------------------------------------------------------- the rules

def test_a_guessed_domain_that_redirects_away_is_refused():
    """customgpt.io redirects to a domain marketplace; its icon is not CustomGPT's."""
    assert brand._same_site("unstoppabledomains.com", "customgpt.io") is False


def test_a_subdomain_of_the_site_asked_for_is_accepted():
    assert brand._same_site("www.ragie.ai", "ragie.ai") is True
    assert brand._same_site("cdn.assets.customgpt.ai", "customgpt.ai") is True


def test_a_code_host_is_never_treated_as_the_vendor():
    """github.com/opf/openproject would otherwise publish GitHub's octocat."""
    assert brand.resolve("https://github.com/opf/openproject") is None
    assert brand.resolve("https://pypi.org/project/anything/") is None


def test_a_project_site_hosted_on_a_platform_is_still_the_project(monkeypatch):
    """A *.github.io page is the project's own; only the code host is refused."""
    monkeypatch.setattr(brand, "_host", lambda url: "tesseract-ocr.github.io")
    # It gets past the code-host check and goes on to fetch, which is the point.
    assert brand._site("tesseract-ocr.github.io") not in brand._GENERIC_HOSTS


def test_declared_icons_are_read_largest_first():
    html = ('<html><head>'
            '<link rel="icon" href="/small.png" sizes="16x16">'
            '<link rel="icon" href="/big.png" sizes="192x192">'
            '</head><body></body></html>')
    assert brand._declared_icons(html, "https://x.test/") == [
        "https://x.test/big.png", "https://x.test/small.png",
    ]


def test_declared_icons_are_found_however_much_css_precedes_them():
    """customgpt.ai inlines ~400 KB of CSS before its icon link."""
    bulk = "<style>" + ("a{}" * 90_000) + "</style>"
    html = f'<html><head>{bulk}<link rel="icon" href="/f.png"></head><body></body></html>'
    assert brand._declared_icons(html, "https://x.test/") == ["https://x.test/f.png"]


def test_a_link_tag_in_the_body_is_not_a_declaration():
    html = '<html><head></head><body><link rel="icon" href="/nope.png"></body></html>'
    assert brand._declared_icons(html, "https://x.test/") == []


def test_a_mark_becomes_a_data_uri_because_that_is_what_the_csp_allows():
    uri = brand.data_uri(b"\x89PNG\r\n", "png")
    assert uri.startswith("data:image/png;base64,")


# ------------------------------------------------------------------- the cache

def test_a_resolved_mark_is_fetched_once_and_then_served_from_disk(tmp_path):
    calls = []

    def resolver(url):
        calls.append(url)
        return b"x" * 500, "png"

    cache = brand.LogoCache(str(tmp_path))
    first = cache.get("ragie", "https://ragie.ai/docs", resolver=resolver)
    second = cache.get("ragie", "https://ragie.ai/docs", resolver=resolver)

    assert first == second == (b"x" * 500, "png")
    assert len(calls) == 1, "a cached mark must not be fetched again"


def test_a_vendor_with_no_mark_is_asked_about_once_not_once_per_render(tmp_path):
    calls = []

    def resolver(url):
        calls.append(url)
        return None

    cache = brand.LogoCache(str(tmp_path))
    assert cache.get("obscure", "https://obscure.test/docs", resolver=resolver) is None
    assert cache.get("obscure", "https://obscure.test/docs", resolver=resolver) is None
    assert len(calls) == 1


def test_a_negative_result_is_retried_once_it_goes_stale(tmp_path):
    calls = []
    cache = brand.LogoCache(str(tmp_path))
    cache.get("later", "https://later.test/docs", resolver=lambda url: calls.append(url))

    marker = tmp_path / "later.missing"
    stale = time.time() - brand.NEGATIVE_TTL_S - 60
    import os
    os.utime(marker, (stale, stale))

    cache.get("later", "https://later.test/docs", resolver=lambda url: calls.append(url))
    assert len(calls) == 2


def test_a_resolver_that_raises_never_reaches_the_caller(tmp_path):
    """A logo is decoration; failing to fetch one must not disturb a page."""
    def boom(url):
        raise RuntimeError("network down")

    assert brand.LogoCache(str(tmp_path)).get("x", "https://x.test/", resolver=boom) is None


def test_a_candidate_with_no_docs_url_is_never_fetched_for(tmp_path):
    calls = []
    cache = brand.LogoCache(str(tmp_path))

    assert cache.get("bare", "", resolver=lambda url: calls.append(url)) is None
    assert calls == []


# ---------------------------------------------------------------- the endpoint

def test_the_endpoint_only_resolves_names_this_tenant_has_benchmarked(monkeypatch):
    """It must never be pointed at a host of the caller's choosing."""
    import server.main as main_module
    from fastapi.testclient import TestClient
    from server.test_backend_hardening import headers  # noqa: F401

    asked = []

    def fake_get(name, docs_url, **kwargs):
        asked.append((name, docs_url))
        return b"logo-bytes-long-enough", "png"

    monkeypatch.setattr(main_module._LOGOS, "get", fake_get)
    monkeypatch.setattr(main_module.runs, "candidate_docs_urls",
                        lambda owner: {"known_tool": "https://known.test/docs"})

    with TestClient(main_module.app) as client:
        response = client.get("/api/brand?names=known_tool,evil_tool",
                              headers={"Authorization": "Bearer token-a"})

    assert response.status_code == 200
    assert list(response.json()["logos"]) == ["known_tool"]
    assert asked == [("known_tool", "https://known.test/docs")]


def test_the_endpoint_bounds_how_many_names_one_request_can_ask_for(monkeypatch):
    import server.main as main_module
    from fastapi.testclient import TestClient

    known = {f"tool_{i}": "https://x.test/docs" for i in range(100)}
    asked = []
    monkeypatch.setattr(main_module.runs, "candidate_docs_urls", lambda owner: known)
    monkeypatch.setattr(main_module._LOGOS, "get",
                        lambda name, url, **kw: asked.append(name) or None)

    with TestClient(main_module.app) as client:
        client.get("/api/brand?names=" + ",".join(known),
                   headers={"Authorization": "Bearer token-a"})

    assert len(asked) == main_module._MAX_LOGO_NAMES
