import json
from types import SimpleNamespace

import pytest

from engine import integration_agent, integration_tools

ENV = {"OPENROUTER_API_KEY": "llm-secret", "SCRAPEDO_API_TOKEN": "scraper-secret"}


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


def _call(name, **arguments):
    """One tool call in the shape an OpenAI-compatible client returns."""
    return SimpleNamespace(
        id=f"call_{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _agent(script, monkeypatch, search=None, read=None):
    """Drive the loop from a script of model turns.

    Each entry is either a list of tool calls the model makes on that turn, or a
    string it answers with. Everything the model was sent is recorded, which is
    how the secret tests prove a value never left this process.
    """
    monkeypatch.setattr(
        integration_agent.docs_intel, "web_search",
        search or (lambda query, n, env: [
            {"title": "Vendor docs", "url": "https://docs.vendor.test/api"}]))
    monkeypatch.setattr(
        integration_agent.docs_intel, "scrape_page",
        read or (lambda url, env: "Authenticate with a bearer token."))

    turns = list(script)
    sent = []

    class Completions:
        def create(self, **kwargs):
            sent.append(kwargs)
            turn = turns.pop(0) if turns else "Done."
            if isinstance(turn, str):
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=turn, tool_calls=None))])
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="", tool_calls=turn))])

    monkeypatch.setattr(
        integration_agent, "chat_client",
        lambda provider, env: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    return sent


class RecordingActions(integration_tools.Actions):
    """A caller that accepts every write and remembers it."""

    def __init__(self, env=None):
        self.saved = {}
        self.removed = []
        self.order = None
        self._env = dict(env or ENV)

    def save_credential(self, env, value):
        self.saved[env] = value
        self._env[env] = value
        return f"Stored the operator's key as {env}."

    def save_setting(self, env, value):
        self.saved[env] = value
        self._env[env] = value
        return f"Set {env} to {value}."

    def remove_setting(self, env):
        self.removed.append(env)
        self._env.pop(env, None)
        return f"Cleared {env}."

    def set_scraper_order(self, order):
        self.order = list(order)
        return "Scraper order is now " + " ".join(order) + "."

    def environment(self):
        return dict(self._env)


def test_respond_fails_closed_when_prerequisites_are_missing():
    with pytest.raises(RuntimeError, match="prerequisites"):
        integration_agent.respond("Integrate Vendor Cloud", {})


def test_a_pasted_key_is_stored_without_ever_reaching_the_model(monkeypatch):
    # The whole point of the vault. The operator pastes a key in the chat; the
    # agent stores it by reference and the value appears in exactly one place —
    # the write itself.
    key = "000000a000000f0000a00000f00bdb000a0000a00ee"
    sent = _agent([
        [_call("save_credential", env="SCRAPEDO_API_TOKEN", secret_ref="pasted_secret_1")],
        "Saved your Scrape.do key.",
    ], monkeypatch)
    actions = RecordingActions()

    result = integration_agent.respond(
        f"add this api key to scrape.do: {key}", ENV, actions=actions)

    assert actions.saved == {"SCRAPEDO_API_TOKEN": key}
    assert result["implementation"] == {"status": "applied", "summary": "Saved SCRAPEDO_API_TOKEN"}
    # Not in the prompt, not in a tool result, not anywhere the model was sent.
    assert key not in json.dumps(sent, default=str)
    assert "pasted_secret_1" in json.dumps(sent, default=str)


def test_a_reference_the_operator_never_pasted_is_refused(monkeypatch):
    # A model that invents a handle must not be able to store an empty or
    # guessed value under a real variable name.
    _agent([
        [_call("save_credential", env="SCRAPEDO_API_TOKEN", secret_ref="pasted_secret_9")],
        "I could not find a key you pasted.",
    ], monkeypatch)
    actions = RecordingActions()

    integration_agent.respond("set up scrape.do", ENV, actions=actions)

    assert actions.saved == {}


def test_a_reference_that_is_not_a_reference_is_refused(monkeypatch):
    # The obvious attack on the vault: pass the literal value as the "reference".
    _agent([
        [_call("save_credential", env="SCRAPEDO_API_TOKEN", secret_ref="sk-live-abcdef123456")],
        "That did not work.",
    ], monkeypatch)
    actions = RecordingActions()

    integration_agent.respond("set up scrape.do", ENV, actions=actions)

    assert actions.saved == {}


def test_a_read_only_caller_is_offered_no_write_tools(monkeypatch):
    sent = _agent(["Scrape.do already ships here."], monkeypatch)

    integration_agent.respond("is scrape.do implemented", ENV)

    offered = {tool["function"]["name"] for tool in sent[0]["tools"]}
    assert "save_credential" not in offered
    assert "set_scraper_order" not in offered
    # Reading and asking are never withdrawn: an agent that cannot write still
    # has to be able to answer, and to hand the operator a field.
    assert {"deployment_state", "search_documentation", "request_credential"} <= offered


def test_the_agent_can_reorder_the_scraper_chain(monkeypatch):
    _agent([
        [_call("set_scraper_order", order=["oxylabs", "scrapedo"])],
        "Oxylabs is now tried first.",
    ], monkeypatch)
    actions = RecordingActions()

    result = integration_agent.respond("prefer oxylabs for scraping", ENV, actions=actions)

    assert actions.order == ["oxylabs", "scrapedo"]
    assert result["implementation"]["status"] == "applied"


def test_a_write_the_caller_refuses_becomes_a_correction_not_a_crash(monkeypatch):
    """A rejected write has to reach the model as text it can act on.

    Raising instead would spend the operator's turn on a stack trace when the
    only problem is a misspelled variable the agent could have fixed itself.
    """
    class Refusing(RecordingActions):
        def save_setting(self, env, value):
            if env == "NONSENSE":
                raise ValueError(f"{env} is not a provider setting this deployment accepts")
            return super().save_setting(env, value)

    sent = _agent([
        [_call("save_setting", env="NONSENSE", value="x")],
        [_call("save_setting", env="OPENROUTER_MODEL", value="openai/gpt-4o-mini")],
        "Set the OpenRouter model.",
    ], monkeypatch)
    actions = Refusing()

    result = integration_agent.respond("use gpt-4o-mini", ENV, actions=actions)

    assert "Refused" in json.dumps(sent, default=str)
    assert result["implementation"]["status"] == "applied"


def test_sources_are_collected_from_the_pages_the_agent_actually_read(monkeypatch):
    _agent([
        [_call("search_documentation", query="Vendor Cloud API")],
        [_call("read_documentation", url="https://docs.vendor.test/api")],
        "Vendor Cloud is OpenAI-compatible.",
    ], monkeypatch)

    result = integration_agent.respond("add Vendor Cloud", ENV)

    assert result["sources"] == [
        {"title": "docs.vendor.test", "url": "https://docs.vendor.test/api"}]


def test_a_plaintext_documentation_url_is_never_fetched(monkeypatch):
    def refuse(url, env):
        raise AssertionError("an http URL must not be fetched")

    _agent([
        [_call("read_documentation", url="http://insecure.test/docs")],
        "That page could not be used.",
    ], monkeypatch, read=refuse)

    result = integration_agent.respond("add Vendor Cloud", ENV)

    assert result["sources"] == []


def test_request_credential_puts_a_field_in_front_of_the_operator(monkeypatch):
    _agent([
        [_call("request_credential", env="mistral_api_key", label="Mistral")],
        "Paste your Mistral key below.",
    ], monkeypatch)

    result = integration_agent.respond("add support for Mistral", ENV)

    # Normalized to the exact name the credentials endpoint accepts, so the UI
    # can offer it directly instead of asking the operator to spell it.
    assert result["credential"] == {"env": "MISTRAL_API_KEY", "label": "Mistral"}


@pytest.mark.parametrize("arguments", [
    {"env": "PATH", "label": "System"},
    {"env": "MISTRAL_TOKEN", "label": "Mistral"},
    {"label": "Mistral"},
])
def test_a_credential_name_that_is_not_a_provider_key_is_dropped(arguments, monkeypatch):
    # A model may name anything; only a well-formed provider credential variable
    # is offered as somewhere to put a secret.
    _agent([
        [_call("request_credential", **arguments)],
        "I could not name that variable.",
    ], monkeypatch)

    result = integration_agent.respond("add support for Mistral", ENV)

    assert "credential" not in result


def test_an_answer_with_no_writes_is_not_reported_as_applied(monkeypatch):
    _agent([
        [_call("deployment_state")],
        "Scrape.do is implemented as a scraping provider.",
    ], monkeypatch)

    result = integration_agent.respond("is scrape.do implemented", ENV)

    assert result["implementation"] == {"status": "answer", "summary": "Answered."}
    assert result["message"] == "Scrape.do is implemented as a scraping provider."


def test_deployment_state_reports_configuration_without_any_value(monkeypatch):
    sent = _agent([[_call("deployment_state")], "Answered."], monkeypatch)

    integration_agent.respond("what is configured", ENV)

    # The facts blob travels as a tool result, so this is the real check that no
    # value rides along with the names.
    payload = json.dumps(sent[-1], default=str)
    assert "llm-secret" not in payload and "scraper-secret" not in payload
    assert "SCRAPEDO_API_TOKEN" in payload and "scrapedo" in payload


def test_configured_secrets_are_still_redacted_out_of_the_request(monkeypatch):
    sent = _agent(["Answered."], monkeypatch)

    integration_agent.respond("why is llm-secret failing", ENV)

    assert "llm-secret" not in json.dumps(sent, default=str)


def test_a_runaway_tool_loop_still_ends_in_an_answer(monkeypatch):
    # A model that never stops calling tools must not produce an empty turn.
    sent = _agent(
        [[_call("deployment_state")]] * integration_agent.MAX_TOOL_TURNS,
        monkeypatch)

    result = integration_agent.respond("what is configured", ENV)

    assert result["message"] == "Done."
    # The closing request carries no tools, which is what forces a reply.
    assert "tools" not in sent[-1]


def test_the_deployment_facts_name_the_chain_order_and_effective_model():
    facts = integration_agent._deployment_facts({
        "OPENAI_API_KEY": "a-real-secret-value",
        "SCRAPEDO_API_TOKEN": "another-real-secret",
        "OPENROUTER_MODEL": "openai/gpt-4o-mini",
    })

    assert "a-real-secret-value" not in facts
    assert "another-real-secret" not in facts
    assert "OPENAI_API_KEY" in facts and "no credential" in facts
    assert "brightdata" in facts
    assert "Scraper chain order" in facts
    assert "openai/gpt-4o-mini" in facts


def _single_reply(content: str, monkeypatch):
    """One canned model reply, for the single-shot `suggest_values` path."""
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

    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=content))
            ])

    monkeypatch.setattr(
        integration_agent,
        "chat_client",
        lambda provider, env: SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        ),
    )
    return dict(ENV)


SUGGESTIONS = (
    '{"summary":"Pick by cost.","options":['
    '{"value":"openai/gpt-4o-mini","note":"cheapest"},'
    '{"value":"openai/gpt-4o-mini","note":"duplicate"},'
    '{"value":"a model with spaces","note":"not a token"},'
    '{"value":"anthropic/claude-sonnet-4","note":"strongest"}]}'
)


def test_suggest_values_returns_deduped_single_token_model_ids(monkeypatch):
    env = _single_reply(SUGGESTIONS, monkeypatch)

    result = integration_agent.suggest_values("openrouter_model", env)

    assert result["env"] == "OPENROUTER_MODEL"
    assert [item["value"] for item in result["options"]] == [
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4",
    ]
    assert result["summary"] == "Pick by cost."


def test_suggest_values_keeps_only_https_urls_for_a_base_url_setting(monkeypatch):
    env = _single_reply(
        '{"summary":"Regional endpoints.","options":['
        '{"value":"http://api.vendor.test/v1","note":"plaintext"},'
        '{"value":"not-a-url","note":"nonsense"},'
        '{"value":"https://api.vendor.test/v1","note":"default"}]}',
        monkeypatch,
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

    integration_agent.suggest_values("OPENAI_ORCHESTRATOR_MODEL", dict(ENV))

    assert "Orchestrator" not in queries[0]
    assert queries[0].startswith("Openai API model ids")
