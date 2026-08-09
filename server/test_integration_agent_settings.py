import pytest

from engine import integration_agent
from server.test_backend_hardening import client, headers  # noqa: F401


@pytest.fixture(autouse=True)
def clear_agent_provider_environment(monkeypatch):
    for name in (
        "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
        "SCRAPEDO_API_TOKEN",
        "OXYLABS_USERNAME", "OXYLABS_PASSWORD",
        "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_SERP_ZONE",
        "BRIGHTDATA_UNLOCKER_ZONE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_status_and_messages_fail_closed_without_both_prerequisites(client):
    status = client.get(
        "/api/settings/integration-agent",
        headers=headers("token-a"),
    )
    assert status.status_code == 200
    assert status.json()["ready"] is False
    assert set(status.json()["missing"]) == {"default_llm", "web_scraper"}

    blocked = client.post(
        "/api/settings/integration-agent/messages",
        headers=headers("token-a"),
        json={"message": "Integrate Vendor Cloud"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "integration_agent_unavailable"


def test_ready_agent_uses_the_tenant_provider_snapshot(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "deployment-llm-secret")
    monkeypatch.setenv("SCRAPEDO_API_TOKEN", "deployment-scraper-secret")
    captured = {}

    def fake_respond(message, env, history, actions=None):
        captured.update({"message": message, "env": env, "history": history,
                         "actions": actions})
        return {
            "message": "A bounded connector proposal is ready.",
            "sources": [{"title": "Docs", "url": "https://vendor.test/docs"}],
            "implementation": {
                "status": "proposal",
                "summary": "Proposal generated.",
            },
        }

    monkeypatch.setattr(integration_agent, "respond", fake_respond)

    status = client.get(
        "/api/settings/integration-agent",
        headers=headers("token-a"),
    ).json()
    assert status["ready"] is True
    assert status["llm"]["provider"] == "openrouter"
    assert status["scraper"]["provider"] == "scrapedo"

    response = client.post(
        "/api/settings/integration-agent/messages",
        headers=headers("token-a"),
        json={
            "message": "What about its batch endpoint?",
            "history": [
                {"role": "user", "content": "Integrate Vendor Cloud"},
                {"role": "assistant", "content": "I found its API documentation."},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["implementation"]["status"] == "proposal"
    assert captured["message"] == "What about its batch endpoint?"
    assert captured["history"][0]["content"] == "Integrate Vendor Cloud"
    assert captured["env"]["OPENROUTER_API_KEY"] == "deployment-llm-secret"
    assert "deployment-llm-secret" not in response.text
    assert "deployment-scraper-secret" not in response.text


def test_the_agent_is_handed_writes_bound_to_the_calling_tenant(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "deployment-llm-secret")
    monkeypatch.setenv("SCRAPEDO_API_TOKEN", "deployment-scraper-secret")
    seen = {}

    def fake_respond(message, env, history, actions=None):
        seen["result"] = actions.save_credential("MISTRAL_API_KEY", "pasted-value")
        seen["environment"] = actions.environment()
        return {"message": "Saved.", "sources": [],
                "implementation": {"status": "applied", "summary": "Saved MISTRAL_API_KEY"}}

    monkeypatch.setattr(integration_agent, "respond", fake_respond)

    response = client.post(
        "/api/settings/integration-agent/messages",
        headers=headers("token-a"),
        json={"message": "save my mistral key"},
    )

    assert response.status_code == 200
    assert seen["environment"]["MISTRAL_API_KEY"] == "pasted-value"
    # Stored for this tenant only, and readable back through the ordinary
    # listing rather than through anything the agent path invented.
    keys = client.get("/api/settings/provider-keys", headers=headers("token-a")).json()["keys"]
    assert any(key["env"] == "MISTRAL_API_KEY" and key["source"] == "settings" for key in keys)
    other = client.get("/api/settings/provider-keys", headers=headers("token-b")).json()["keys"]
    assert not any(key["env"] == "MISTRAL_API_KEY" for key in other)
    assert "pasted-value" not in response.text


@pytest.mark.parametrize("call, argument, expected", [
    ("save_credential", ("PATH", "x"), "not a provider setting"),
    ("save_credential", ("OPENROUTER_MODEL", "x"), "not a credential"),
    ("save_setting", ("MISTRAL_API_KEY", "x"), "holds a secret"),
    ("save_setting", ("VENDOR_BASE_URL", "http://insecure.test"), "HTTPS"),
    ("remove_setting", ("PATH",), "not a provider setting"),
])
def test_a_write_the_endpoint_would_refuse_is_refused_here_too(
    client, monkeypatch, call, argument, expected
):
    """The agent gets the endpoint's validation, not a private, laxer copy.

    Rejections arrive as ValueError so the agent reads the reason mid-turn and
    can correct itself, but the rules being enforced are the same ones.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "deployment-llm-secret")
    monkeypatch.setenv("SCRAPEDO_API_TOKEN", "deployment-scraper-secret")
    failures = {}

    def fake_respond(message, env, history, actions=None):
        try:
            getattr(actions, call)(*argument)
        except ValueError as exc:
            failures["reason"] = str(exc)
        return {"message": "Refused.", "sources": [],
                "implementation": {"status": "answer", "summary": "Answered."}}

    monkeypatch.setattr(integration_agent, "respond", fake_respond)
    client.post("/api/settings/integration-agent/messages",
                headers=headers("token-a"), json={"message": "do it"})

    assert expected in failures["reason"]


def test_integration_agent_requires_authentication(client):
    assert client.get("/api/settings/integration-agent").status_code == 401
    assert client.post(
        "/api/settings/integration-agent/messages",
        json={"message": "Integrate Vendor Cloud"},
    ).status_code == 401
