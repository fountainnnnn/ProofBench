"""Which provider answers first, and what happens when it does not.

Three providers reach the same public documentation and measured equivalent
extracted text, so the order is about latency and cost — scrape.do at a 2.9s
median against 14.2s and 49s for the same 25 results. That makes the order a
preference an operator changes, not a constant, and these tests pin the two
properties that keep a changed preference safe: a stored value can never stop a
deployment scraping, and every configured provider is still tried.
"""
from __future__ import annotations

import pytest

from engine import docs_intel, scrapers
from engine.test_docs_fallback import offline_dns  # noqa: F401  (URL policy without live DNS)

ALL = {
    "OXYLABS_USERNAME": "u", "OXYLABS_PASSWORD": "p",
    "SCRAPEDO_API_TOKEN": "t",
    "BRIGHTDATA_API_TOKEN": "t", "BRIGHTDATA_SERP_ZONE": "s",
    "BRIGHTDATA_UNLOCKER_ZONE": "w",
}


# ------------------------------------------------------------------- the order

def test_the_default_leads_with_the_fastest_measured_provider():
    assert scrapers.DEFAULT_ORDER == ("scrapedo", "oxylabs", "brightdata")


def test_an_operator_order_is_honoured():
    assert scrapers.parse_order("brightdata oxylabs scrapedo") == (
        "brightdata", "oxylabs", "scrapedo")
    assert scrapers.parse_order(["oxylabs", "scrapedo"])[0] == "oxylabs"


def test_a_provider_left_out_is_demoted_not_removed():
    """Dropping a fallback an operator still holds credentials for is not a
    setting anyone asks for; being tried last is."""
    order = scrapers.parse_order("brightdata")
    assert order[0] == "brightdata"
    assert set(order) == set(scrapers.DEFAULT_ORDER)


@pytest.mark.parametrize("value", ["", None, "nonsense", ["nope"], 42, ["oxylabs", "oxylabs"]])
def test_a_bad_stored_value_can_never_disable_scraping(value):
    assert set(scrapers.parse_order(value)) == set(scrapers.DEFAULT_ORDER)


def test_only_providers_holding_credentials_are_tried():
    assert scrapers.configured_providers({"SCRAPEDO_API_TOKEN": "t"}, "search") == ["scrapedo"]
    assert scrapers.configured_providers({"OXYLABS_USERNAME": "u"}, "search") == []
    assert scrapers.configured_providers({}, "scrape") == []


def test_the_order_travels_in_the_runtime_environment():
    env = {scrapers.ORDER_ENV: "oxylabs scrapedo brightdata"}
    assert scrapers.order_from_env(env)[0] == "oxylabs"


# ------------------------------------------------------------------- the search

def test_search_asks_providers_in_the_configured_order(monkeypatch):
    asked = []

    def stub(name, rows):
        def call(query, n=10, env=None):
            asked.append(name)
            return rows
        return call

    from engine import brightdata, scrapedo
    monkeypatch.setattr(scrapedo, "web_search", stub("scrapedo", []))
    monkeypatch.setattr(docs_intel, "oxylabs_search",
                        lambda q, n=5, env=None: asked.append("oxylabs") or [])
    monkeypatch.setattr(brightdata, "web_search",
                        stub("brightdata", [{"title": "t", "url": "u", "snippet": ""}]))

    rows = docs_intel.web_search("q", n=10, env=dict(ALL))
    assert asked == ["scrapedo", "oxylabs", "brightdata"]
    assert rows and rows[0]["title"] == "t"


def test_an_empty_answer_is_treated_as_a_failure_and_the_chain_continues(monkeypatch):
    """One provider answering 200 with no rows must not end the search while
    another is still available — an empty search ends an intake turn."""
    from engine import brightdata, scrapedo
    monkeypatch.setattr(scrapedo, "web_search", lambda q, n=10, env=None: [])
    monkeypatch.setattr(docs_intel, "oxylabs_search",
                        lambda q, n=5, env=None: [{"title": "second", "url": "u", "snippet": ""}])
    monkeypatch.setattr(brightdata, "web_search",
                        lambda q, n=10, env=None: pytest.fail("should have stopped at oxylabs"))

    assert docs_intel.web_search("q", n=10, env=dict(ALL))[0]["title"] == "second"


def test_a_misconfigured_deployment_fails_loudly(monkeypatch):
    """"No tools found" would read as an answer about the internet rather than
    about this install."""
    with pytest.raises(RuntimeError, match="no search provider is configured"):
        docs_intel.web_search("q", n=10, env={})


def test_every_provider_failing_names_them_without_echoing_their_errors(monkeypatch):
    from engine import brightdata, scrapedo

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret-provider-detail")

    monkeypatch.setattr(scrapedo, "web_search", boom)
    monkeypatch.setattr(docs_intel, "oxylabs_search", boom)
    monkeypatch.setattr(brightdata, "web_search", boom)

    with pytest.raises(RuntimeError) as raised:
        docs_intel.web_search("q", n=10, env=dict(ALL))
    assert "secret-provider-detail" not in str(raised.value)
    assert "scrapedo" in str(raised.value)


# ------------------------------------------------------------------- the scrape

def test_scrape_uses_the_first_provider_that_answers(monkeypatch, offline_dns):
    from engine import brightdata, scrapedo
    monkeypatch.setattr(scrapedo, "scrape",
                        lambda url, env=None: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(docs_intel, "oxylabs_scrape",
                        lambda url, settings: "<p>from oxylabs</p>")
    monkeypatch.setattr(brightdata, "scrape",
                        lambda url, env=None: pytest.fail("oxylabs already answered"))

    text = docs_intel.scrape_page("https://docs.example.com/a", dict(ALL))
    assert "from oxylabs" in text


def test_scrape_falls_through_to_the_bounded_direct_fetch(monkeypatch, offline_dns):
    from engine import brightdata, scrapedo

    def boom(*_args, **_kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(scrapedo, "scrape", boom)
    monkeypatch.setattr(docs_intel, "oxylabs_scrape", boom)
    monkeypatch.setattr(brightdata, "scrape", boom)
    monkeypatch.setattr(docs_intel, "fetch_documentation", lambda url: "direct text")

    assert docs_intel.scrape_page("https://docs.example.com/a", dict(ALL)) == "direct text"
