from types import SimpleNamespace

import pytest

from engine import integration_agent

PLAN_RESEARCH = (
    '{"thought":"An outside vendor detail, so I will read its documentation.",'
    '"action":"research","query":"Vendor Cloud API documentation"}'
)


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

    # Two model calls now: the plan, then the proposal. The plan chooses
    # research, which is what this test is about.
    replies = [
        PLAN_RESEARCH,
        '{"message":"Use a manifest-only connector.",'
        '"implementation":{"status":"proposal",'
        '"summary":"Connector proposal generated."}}',
    ]

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=replies.pop(0)))
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


def _agent_returning(content: str, monkeypatch, plan: bool = True):
    """Wire the agent to canned model replies, with research stubbed out.

    `respond` makes two calls (plan, then compose); `suggest_values` makes one.
    """
    monkeypatch.setattr(
        integration_agent.docs_intel,
        "web_search",
        lambda query, n, env: [{"title": "Vendor docs", "url": "https://docs.vendor.test/api"}],
    )
    monkeypatch.setattr(
        integration_agent.docs_intel,
        "scrape_page",
        lambda url, env: "Authenticate with a bearer token.",
    )

    replies = [PLAN_RESEARCH, content] if plan else [content]

    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=replies.pop(0)))
            ])

    monkeypatch.setattr(
        integration_agent,
        "chat_client",
        lambda provider, env: SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        ),
    )
    return {"OPENROUTER_API_KEY": "llm-secret", "SCRAPEDO_API_TOKEN": "scraper-secret"}


BASE_REPLY = ('"message":"Use a manifest-only connector.",'
              '"implementation":{"status":"proposal","summary":"Connector proposal generated."}')


def test_respond_names_the_variable_an_operator_key_belongs_in(monkeypatch):
    env = _agent_returning(
        '{' + BASE_REPLY + ',"credential":{"env":"mistral_api_key","label":"Mistral"}}',
        monkeypatch,
    )

    result = integration_agent.respond("Add support for Mistral", env)

    # Normalized to the exact name the credentials endpoint accepts, so the UI
    # can offer it directly instead of asking the operator to spell it.
    assert result["credential"] == {"env": "MISTRAL_API_KEY", "label": "Mistral"}


@pytest.mark.parametrize("credential", [
    '"credential":{"env":"PATH","label":"System"}',
    '"credential":{"env":"MISTRAL_TOKEN","label":"Mistral"}',
    '"credential":"MISTRAL_API_KEY"',
    '"credential":{"label":"Mistral"}',
])
def test_respond_drops_a_credential_name_that_is_not_a_provider_api_key(credential, monkeypatch):
    # A model may name anything; only a well-formed provider API key variable
    # is offered as somewhere to put a secret.
    env = _agent_returning('{' + BASE_REPLY + ',' + credential + '}', monkeypatch)

    result = integration_agent.respond("Add support for Mistral", env)

    assert "credential" not in result
    assert result["implementation"]["status"] == "proposal"


SUGGESTIONS = (
    '{"summary":"Pick by cost.","options":['
    '{"value":"openai/gpt-4o-mini","note":"cheapest"},'
    '{"value":"openai/gpt-4o-mini","note":"duplicate"},'
    '{"value":"a model with spaces","note":"not a token"},'
    '{"value":"anthropic/claude-sonnet-4","note":"strongest"}]}'
)


def test_suggest_values_returns_deduped_single_token_model_ids(monkeypatch):
    env = _agent_returning(SUGGESTIONS, monkeypatch, plan=False)

    result = integration_agent.suggest_values("openrouter_model", env)

    assert result["env"] == "OPENROUTER_MODEL"
    assert [item["value"] for item in result["options"]] == [
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4",
    ]
    assert result["summary"] == "Pick by cost."


def test_suggest_values_keeps_only_https_urls_for_a_base_url_setting(monkeypatch):
    env = _agent_returning(
        '{"summary":"Regional endpoints.","options":['
        '{"value":"http://api.vendor.test/v1","note":"plaintext"},'
        '{"value":"not-a-url","note":"nonsense"},'
        '{"value":"https://api.vendor.test/v1","note":"default"}]}',
        monkeypatch,
        plan=False,
    )

    result = integration_agent.suggest_values("VENDOR_BASE_URL", env)

    assert [item["value"] for item in result["options"]] == ["https://api.vendor.test/v1"]


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "OXYLABS_PASSWORD", "PATH", ""])
def test_suggest_values_refuses_anything_that_is_not_a_model_or_base_url(name):
    # A secret has no published value, and offering to research one would be a
    # lie about what the agent can do.
    with pytest.raises(ValueError, match="MODEL or BASE_URL"):
        integration_agent.suggest_values(name, {"OPENROUTER_API_KEY": "k"})


def test_suggest_values_researches_the_vendor_not_the_proofbench_role(monkeypatch):
    # OPENAI_ORCHESTRATOR_MODEL names OpenAI plus the role it fills here. Only
    # the vendor has documentation, so only the vendor is searched for.
    queries = []
    monkeypatch.setattr(
        integration_agent.docs_intel,
        "web_search",
        lambda query, n, env: queries.append(query) or [],
    )

    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"summary":"","options":[]}'))])

    monkeypatch.setattr(
        integration_agent,
        "chat_client",
        lambda provider, env: SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        ),
    )

    integration_agent.suggest_values(
        "OPENAI_ORCHESTRATOR_MODEL",
        {"OPENROUTER_API_KEY": "k", "SCRAPEDO_API_TOKEN": "t"},
    )

    assert "Orchestrator" not in queries[0]
    assert queries[0].startswith("Openai API model ids")


def _planning_agent(plan_reply: str, monkeypatch):
    """Wire only the planner, and make any research a hard failure.

    A request the deployment facts already settle must not reach the network.
    Blowing up on contact is the only way a test can prove that.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("a question about shipped behaviour must not be researched")

    monkeypatch.setattr(integration_agent.docs_intel, "web_search", refuse)
    monkeypatch.setattr(integration_agent.docs_intel, "scrape_page", refuse)

    class Completions:
        def create(self, **kwargs):
            Completions.prompt = kwargs["messages"][-1]["content"]
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=plan_reply))
            ])

    monkeypatch.setattr(
        integration_agent,
        "chat_client",
        lambda provider, env: SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        ),
    )
    return Completions


def test_a_question_about_shipped_behaviour_is_answered_without_research(monkeypatch):
    planner = _planning_agent(
        '{"thought":"Scrape.do ships as a scraper, so no research is needed.",'
        '"action":"answer","answer":"Scrape.do is implemented as a scraping provider."}',
        monkeypatch,
    )

    result = integration_agent.respond(
        "is scrape.do implemented",
        {"OPENROUTER_API_KEY": "llm-secret", "SCRAPEDO_API_TOKEN": "scraper-secret"},
    )

    assert result["message"] == "Scrape.do is implemented as a scraping provider."
    assert result["sources"] == []
    assert result["implementation"]["status"] == "answer"
    # The planner was told what ships, including the scrapers the old prompt
    # never mentioned. That is what makes the question answerable at all.
    assert "scrapedo (Scrape.do)" in planner.prompt
    assert "SCRAPEDO_API_TOKEN" in planner.prompt
    assert "tesseract" in planner.prompt


def test_offering_a_key_for_a_shipped_provider_still_gets_a_field_to_paste_it(monkeypatch):
    # The planner answers this from the facts without researching, and that path
    # used to drop the credential name — so the reply said "set SCRAPEDO_API_TOKEN"
    # and gave the operator nothing to set it with.
    _planning_agent(
        '{"thought":"Scrape.do ships; it just has no token yet.","action":"answer",'
        '"answer":"Scrape.do is already implemented here. Paste the key below.",'
        '"credential":{"env":"SCRAPEDO_API_TOKEN","label":"Scrape.do"}}',
        monkeypatch,
    )

    result = integration_agent.respond("add this api key to scrape.do", {"OPENROUTER_API_KEY": "k",
                                                                         "SCRAPEDO_API_TOKEN": "t"})

    assert result["credential"] == {"env": "SCRAPEDO_API_TOKEN", "label": "Scrape.do"}


def test_an_answer_that_owes_no_key_carries_no_credential_field(monkeypatch):
    _planning_agent(
        '{"thought":"Plain question.","action":"answer","answer":"Scrape.do is implemented."}',
        monkeypatch,
    )

    result = integration_agent.respond(
        "is scrape.do implemented",
        {"OPENROUTER_API_KEY": "k", "SCRAPEDO_API_TOKEN": "t"},
    )

    assert "credential" not in result


def test_deployment_facts_report_configuration_without_any_value(monkeypatch):
    facts = integration_agent._deployment_facts({
        "OPENAI_API_KEY": "a-real-secret-value",
        "SCRAPEDO_API_TOKEN": "another-real-secret",
    })

    assert "a-real-secret-value" not in facts
    assert "another-real-secret" not in facts
    assert "OPENAI_API_KEY" in facts and "no credential" in facts
    assert "brightdata" in facts


def test_a_plan_to_answer_with_nothing_to_say_falls_through_to_research(monkeypatch):
    # An empty answer would render as a blank turn, which is worse than the
    # slower path. A research plan with no query still gets one.
    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"thought":"t","action":"answer","answer":"   "}'))])

    monkeypatch.setattr(
        integration_agent,
        "chat_client",
        lambda provider, env: SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        ),
    )

    plan = integration_agent._plan("add vendor cloud", "facts", "", "openrouter", {})

    assert plan["action"] == "research"
    assert plan["query"]


def test_an_already_shipped_scraper_credential_name_is_offered_verbatim(monkeypatch):
    # SCRAPEDO_API_TOKEN is not an _API_KEY, and rejecting it would send the
    # operator back to guessing the name this exact flow exists to supply.
    env = _agent_returning(
        '{"message":"Scrape.do already ships; it just needs its token.",'
        '"implementation":{"status":"proposal","summary":"Needs a token."},'
        '"credential":{"env":"SCRAPEDO_API_TOKEN","label":"Scrape.do"}}',
        monkeypatch,
    )

    result = integration_agent.respond("set up scrape.do", env)

    assert result["credential"] == {"env": "SCRAPEDO_API_TOKEN", "label": "Scrape.do"}
