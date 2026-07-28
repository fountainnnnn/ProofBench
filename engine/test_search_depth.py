"""Depth is what surfaces a tool that is not already famous.

The first page of Google is the popular answer by construction, so a five-result
search returns a shortlist of whatever is well known — a SharePoint RAG question
came back with the four most searchable options and no self-hosted candidate at
all. Google deprecated `num` in September 2025, so depth means asking for pages.

These tests pin the paging contract and the provider fallback, which is the part
that decides whether a failed search ends an intake turn or merely costs a
retry.
"""
from __future__ import annotations

import json

import pytest

from engine import brightdata, docs_intel

# Oxylabs credentials, because the chain only tries providers that hold them.
OX = {"OXYLABS_USERNAME": "u", "OXYLABS_PASSWORD": "p"}


def _page(*titles: str) -> dict:
    return {"content": {"results": {"organic": [
        {"title": title, "url": f"https://example.com/{title}", "snippet": "s"}
        for title in titles
    ]}}}


def test_more_than_ten_results_asks_for_more_than_one_page(monkeypatch):
    seen = {}

    def fake_query(payload, env):
        seen.update(payload)
        return {"results": [_page("a"), _page("b")]}

    monkeypatch.setattr(docs_intel, "_query", fake_query)
    docs_intel.web_search("rag over sharepoint", n=25, env=dict(OX))

    assert seen["pages"] == 3, "25 results is three pages of ten"
    assert seen["start_page"] == 1


def test_a_small_request_still_costs_a_single_page(monkeypatch):
    seen = {}
    monkeypatch.setattr(docs_intel, "_query",
                        lambda payload, env: seen.update(payload) or {"results": [_page("a")]})
    docs_intel.web_search("q", n=5, env=dict(OX))

    assert seen["pages"] == 1


def test_paging_is_capped_so_one_search_cannot_run_away(monkeypatch):
    seen = {}
    monkeypatch.setattr(docs_intel, "_query",
                        lambda payload, env: seen.update(payload) or {"results": [_page("a")]})
    docs_intel.web_search("q", n=10_000, env=dict(OX))

    assert seen["pages"] == docs_intel.MAX_SEARCH_PAGES


def test_results_from_every_page_are_returned(monkeypatch):
    """Reading results[0] alone silently discarded everything past rank ten."""
    monkeypatch.setattr(docs_intel, "_query", lambda payload, env: {
        "results": [_page("first", "second"), _page("third"), _page("fourth")]})

    titles = [row["title"] for row in docs_intel.web_search("q", n=25, env=dict(OX))]
    assert titles == ["first", "second", "third", "fourth"]


def test_pages_that_overlap_do_not_pad_the_result_count(monkeypatch):
    """The same URL twice is not depth."""
    monkeypatch.setattr(docs_intel, "_query", lambda payload, env: {
        "results": [_page("a", "b"), _page("b", "c")]})

    rows = docs_intel.web_search("q", n=25, env=dict(OX))
    assert [row["title"] for row in rows] == ["a", "b", "c"]


def test_a_page_delivered_as_a_json_string_is_still_read(monkeypatch):
    """Oxylabs sends content as a string when the parser is not engaged."""
    page = {"content": json.dumps({"results": {"organic": [
        {"title": "t", "url": "https://example.com/t", "snippet": "s"}]}})}
    monkeypatch.setattr(docs_intel, "_query", lambda payload, env: {"results": [page]})

    assert docs_intel.web_search("q", n=10, env=dict(OX))[0]["title"] == "t"


# ----------------------------------------------------------------- the failover

def test_a_failed_search_falls_through_to_the_second_provider(monkeypatch):
    """A search returning nothing ends an intake turn, so this must not raise."""
    def boom(payload, env):
        raise RuntimeError("provider down")

    monkeypatch.setattr(docs_intel, "_query", boom)
    monkeypatch.setattr(brightdata, "web_search",
                        lambda query, n=10, env=None: [{"title": "fallback",
                                                        "url": "https://x/", "snippet": ""}])

    rows = docs_intel.web_search("q", n=10, env={**OX,
        "BRIGHTDATA_API_TOKEN": "t", "BRIGHTDATA_SERP_ZONE": "z"})
    assert rows[0]["title"] == "fallback"


def test_without_a_second_provider_the_failure_is_reported(monkeypatch):
    monkeypatch.setattr(docs_intel, "_query",
                        lambda payload, env: (_ for _ in ()).throw(RuntimeError("down")))

    with pytest.raises(RuntimeError):
        docs_intel.web_search("q", n=10, env=dict(OX))


# ------------------------------------------------------- the Bright Data client

def test_brightdata_paginates_with_the_parameter_the_api_actually_requires():
    """`start` alone answers "Error while processing request"; brd_json fixes it."""
    url = brightdata._page_url("rag over sharepoint", 10)
    assert "brd_json=1" in url
    assert "start=10" in url
    assert "q=rag+over+sharepoint" in url


def test_brightdata_normalizes_its_own_field_names(monkeypatch):
    """It says link/description where Oxylabs says url/snippet."""
    monkeypatch.setattr(brightdata, "_request", lambda zone, url, env: json.dumps(
        {"organic": [{"title": "t", "link": "https://x/", "description": "d"}]}))

    rows = brightdata.web_search("q", n=10, env={"BRIGHTDATA_API_TOKEN": "t",
                                                "BRIGHTDATA_SERP_ZONE": "z"})
    assert rows == [{"title": "t", "url": "https://x/", "snippet": "d"}]


def test_an_exhausted_page_reads_as_empty_not_as_an_error(monkeypatch):
    """Bright Data answers a spent page with plain text and HTTP 200."""
    monkeypatch.setattr(brightdata, "_request",
                        lambda zone, url, env: "Error while processing request")

    assert brightdata.web_search("q", n=10, env={"BRIGHTDATA_API_TOKEN": "t",
                                                "BRIGHTDATA_SERP_ZONE": "z"}) == []


def test_brightdata_is_only_used_when_both_token_and_zone_exist():
    assert brightdata.search_configured({"BRIGHTDATA_API_TOKEN": "t",
                                         "BRIGHTDATA_SERP_ZONE": "z"})
    assert not brightdata.search_configured({"BRIGHTDATA_API_TOKEN": "t"})
    assert not brightdata.scrape_configured({"BRIGHTDATA_UNLOCKER_ZONE": "z"})
    assert not brightdata.search_configured({})


def test_the_outbound_host_stays_locked_to_one_api():
    """The target is submitted as data; this process never dials it."""
    assert brightdata.ALLOWED_HOSTS == frozenset({"api.brightdata.com"})
    assert brightdata.ENDPOINT.startswith("https://api.brightdata.com/")
