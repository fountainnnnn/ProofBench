"""The scraper order is an operator preference, so it has to outlive a restart.

Credentials live in a process-local vault, which is right for secrets and wrong
for a preference: one that resets on deploy is a bug. These cover the durable
store behind the Settings control, and the rule that keeps a stored value from
being able to break scraping.
"""
from __future__ import annotations

import pytest

from server import runs
from server.state import SQLiteStore

from server.test_backend_hardening import (  # noqa: F401  (client fixture)
    client, headers,
)


def test_a_preference_survives_a_restart(tmp_path):
    path = str(tmp_path / "state.sqlite3")
    SQLiteStore(path).set_setting("tenant-a", "scraper_order", "brightdata oxylabs scrapedo")

    assert SQLiteStore(path).get_setting("tenant-a", "scraper_order") == (
        "brightdata oxylabs scrapedo")


def test_preferences_do_not_leak_between_tenants(tmp_path):
    store = SQLiteStore(str(tmp_path / "state.sqlite3"))
    store.set_setting("tenant-a", "scraper_order", "oxylabs")

    assert store.get_setting("tenant-b", "scraper_order") is None


def test_setting_the_same_key_twice_replaces_rather_than_duplicates(tmp_path):
    store = SQLiteStore(str(tmp_path / "state.sqlite3"))
    store.set_setting("tenant-a", "scraper_order", "oxylabs")
    store.set_setting("tenant-a", "scraper_order", "brightdata")

    assert store.get_setting("tenant-a", "scraper_order") == "brightdata"


def test_an_unset_order_is_the_measured_default(client):
    assert runs.scraper_order("tenant-a")[0] == "scrapedo"


def test_a_saved_order_is_normalized_before_it_is_stored(client):
    """A name this build does not know must not reach the engine."""
    saved = runs.set_scraper_order("tenant-a", ["brightdata", "not-a-provider"])

    assert saved[0] == "brightdata"
    assert "not-a-provider" not in saved
    # Omitted providers are demoted, never dropped: an operator who still holds
    # credentials for one keeps it as a fallback. The free self-hosted option
    # (SearXNG + Crawl4AI, one entry) trails the paid ones in that set.
    assert set(saved) == {"scrapedo", "oxylabs", "brightdata", "selfhosted"}


# ----------------------------------------------------------------- the endpoint

def test_the_endpoint_reports_the_order_and_which_links_are_ready(client):
    response = client.get("/api/settings/scrapers", headers=headers("token-a"))

    assert response.status_code == 200
    body = response.json()
    assert body["order"][0] == "scrapedo"
    assert body["default"] == ["scrapedo", "oxylabs", "brightdata", "selfhosted"]
    # Each entry says whether it actually holds credentials, so the UI can show
    # a provider that is first in line but cannot answer.
    assert {row["name"] for row in body["providers"]} == set(body["order"])
    assert all("configured" in row and "label" in row for row in body["providers"])


def test_saving_an_order_changes_what_the_endpoint_reports(client):
    put = client.put("/api/settings/scrapers", headers=headers("token-a"),
                     json={"order": ["oxylabs", "brightdata", "scrapedo"]})
    assert put.status_code == 200
    assert put.json()["order"][0] == "oxylabs"

    assert client.get("/api/settings/scrapers",
                      headers=headers("token-a")).json()["order"][0] == "oxylabs"


def test_an_empty_order_is_refused_at_the_schema_boundary(client):
    assert client.put("/api/settings/scrapers", headers=headers("token-a"),
                      json={"order": []}).status_code == 422


def test_the_order_is_scoped_to_the_tenant_that_set_it(client):
    client.put("/api/settings/scrapers", headers=headers("token-a"),
               json={"order": ["brightdata"]})

    other = client.get("/api/settings/scrapers", headers=headers("token-b"))
    assert other.json()["order"][0] == "scrapedo"


def test_the_stored_order_reaches_the_engine_runtime_environment(client):
    import server.main as main_module
    from engine import scrapers

    runs.set_scraper_order("tenant-a", ["oxylabs", "scrapedo", "brightdata"])
    env = main_module.provider_environment("tenant-a")

    assert env[scrapers.ORDER_ENV].split()[0] == "oxylabs"
