from types import SimpleNamespace

import pytest

from engine import integration_agent


@pytest.fixture(autouse=True)
def _no_local_fallback(monkeypatch):
    """These readiness cases are about paid credentials, so the always-on
    self-hosted fallback is switched off. With it on, a keyless deployment is
    legitimately ready in dev mode, which is covered in test_scraper_chain.py."""
    from engine import selfhosted

    monkeypatch.delenv("PROOFBENCH_INSECURE_DEV", raising=False)
    selfhosted.reset_cache()


def test_readiness_requires_codegen_llm_and_one_complete_scraper():
    state = integration_agent.readiness({})
    assert state == {
        "ready": False,
        "llm": {"configured": False, "provider": None},
        "scraper": {"configured": False, "provider": None},
        "missing": ["default_llm", "web_scraper"],
    }

    # One Bright Data zone cannot satisfy an agent that must both search and
    # retrieve documentation.
    partial = integration_agent.readiness({
        "OPENROUTER_API_KEY": "llm-secret",
        "BRIGHTDATA_API_TOKEN": "scraper-secret",
        "BRIGHTDATA_SERP_ZONE": "search-zone",
    })
    assert partial["llm"] == {"configured": True, "provider": "openrouter"}
    assert partial["scraper"]["configured"] is False
    assert partial["missing"] == ["web_scraper"]

    ready = integration_agent.readiness({
        "OPENROUTER_API_KEY": "llm-secret",
        "SCRAPEDO_API_TOKEN": "scraper-secret",
    })
    assert ready["ready"] is True
    assert ready["scraper"] == {"configured": True, "provider": "scrapedo"}


def test_respond_uses_server_collected_sources_and_returns_bounded_proposal(monkeypatch):
    env = {
        "OPENROUTER_API_KEY": "llm-secret",
        "SCRAPEDO_API_TOKEN": "scraper-secret",
    }
    queries = []

    def fake_search(query, n, env):
        queries.append(query)
        return [
            {"title": "Vendor docs", "url": "https://docs.vendor.test/api"},
            {"title": "Rejected", "url": "http://insecure.test/docs"},
        ]

    monkeypatch.setattr(
        integration_agent.docs_intel,
        "web_search",
        fake_search,
    )
    monkeypatch.setattr(
        integration_agent.docs_intel,
        "scrape_page",
        lambda url, env: "OpenAI-compatible chat completions endpoint.",
    )
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=(
                    '{"message":"Use a manifest-only connector.",'
                    '"implementation":{"status":"proposal",'
                    '"summary":"Connector proposal generated."}}'
                )))
            ])

    monkeypatch.setattr(
        integration_agent,
        "chat_client",
        lambda provider, env: SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        ),
    )

    result = integration_agent.respond(
        "Integrate Vendor Cloud with llm-secret",
        env,
        [{"role": "user", "content": "The scraper is scraper-secret"}],
    )

    assert result["sources"] == [{
        "title": "Vendor docs",
        "url": "https://docs.vendor.test/api",
    }]
    assert result["implementation"]["status"] == "proposal"
    assert captured["temperature"] == 0
    assert "llm-secret" not in queries[0]
    assert "llm-secret" not in str(captured)
    assert "scraper-secret" not in str(captured)


def test_respond_fails_closed_when_prerequisites_are_missing():
    with pytest.raises(RuntimeError, match="prerequisites"):
        integration_agent.respond("Integrate Vendor Cloud", {})
